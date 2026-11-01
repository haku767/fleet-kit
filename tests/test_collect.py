import json
import os
import pathlib
import stat
import tempfile
import textwrap
import tomllib
import unittest
from datetime import date, datetime, timedelta, timezone

from fleet_kit import cli, collect, model, sets
from fleet_kit.sets import fantasy

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"
TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()

FLEET = """
[fleet]
name = "test"
owner = "example"
agents_dir = "{agents}"
[page]
set = "fantasy"
[page.labels]
decide = "needs-owner"
[page.fantasy]
budget_dev_usd = 3
[machines.box]
role = "bots"
family = "arch"
home_root = "/srv"
guard = "box-guard"
disks = ["/"]
[machines.laptop]
role = "guard"
family = "nixos"
home_root = "/srv"
guard = "lap-guard"
[stacks.py]
test = "pytest"
allow = ["pytest*"]
[projects.app]
repo = "example/app"
stacks = ["py"]
plan = {{ name = "app-plan", persona = "Ada" }}
dev  = {{ name = "app-dev" }}
[projects.lib]
repo = "other/lib"
stacks = ["py"]
dev  = {{ name = "lib-dev" }}
[projects.notes]
repo = "example/notes"
[guards.box-guard]
machine = "box"
project = "notes"
apps = ["example-box-guard", {{ owner = "other", name = "lib" }}]
[guards.lap-guard]
machine = "laptop"
apps = ["example-lap-guard"]
"""


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class FakeRun:
    """Answers by command: fleet-token, gh (by subcommand and repository), systemctl, nvidia-smi.
    Records every command; `fail` makes a repository's calls fail."""

    def __init__(self):
        self.calls = []
        self.fail = set()
        self.tokens = {None: "tok-default", "lib": "tok-lib"}
        now = datetime.now(timezone.utc)
        self.prs = {"example/app": [
            {"number": 1, "title": "approved and green", "author": {"login": "app/example-app-dev"}, "headRefName": "b1", "headRefOid": "aaa",
             "isDraft": False, "reviewDecision": "APPROVED", "reviews": [], "updatedAt": iso(now), "labels": []},
            {"number": 2, "title": "red by a person", "author": {"login": "someone"}, "headRefName": "b2", "headRefOid": "red",
             "isDraft": False, "reviewDecision": "", "reviews": [], "updatedAt": iso(now), "labels": [{"name": "needs-owner"}]},
            {"number": 3, "title": "a draft", "author": {"login": "app/example-app-dev"}, "headRefName": "b3", "headRefOid": "aaa",
             "isDraft": True, "reviewDecision": "", "reviews": [], "updatedAt": iso(now), "labels": []},
            {"number": 4, "title": "waits for review", "author": {"login": "app/example-app-dev"}, "headRefName": "b4", "headRefOid": "none",
             "isDraft": False, "reviewDecision": "", "reviews": [], "updatedAt": iso(now), "labels": []},
        ], "other/lib": [
            {"number": 7, "title": "nobody reviews here", "author": {"login": "app/example-lib-dev"}, "headRefName": "x", "headRefOid": "none",
             "isDraft": False, "reviewDecision": "", "reviews": [{"author": {"login": "someone"}, "state": "COMMENTED", "submittedAt": iso(now)}],
             "updatedAt": iso(now), "labels": []},
        ], "example/notes": []}
        self.issues = {"example/app": [
            {"number": 10, "title": "ready one", "labels": [{"name": "ready"}], "updatedAt": iso(now)},
            {"number": 11, "title": "live one", "labels": [{"name": "ready"}, {"name": "live"}], "updatedAt": iso(now)},
            {"number": 12, "title": "decide one", "labels": [{"name": "needs-owner"}], "updatedAt": iso(now)},
            {"number": 13, "title": "plain", "labels": [], "updatedAt": iso(now)},
        ], "other/lib": [], "example/notes": []}
        self.checks = {"aaa": ["success", "success"], "red": ["failure"], "none": []}
        self.merged = {"example/app": [{"mergedAt": iso(now), "author": {"login": "app/example-app-dev"}},
                                       {"mergedAt": iso(now - timedelta(days=1)), "author": {"login": "example"}}],
                       "other/lib": [], "example/notes": []}
        self.closed = {"example/app": [{"closedAt": iso(now)}], "other/lib": [], "example/notes": []}
        self.totals = {"example/app": {"is:pr is:merged": 42, "is:issue is:closed": 7, "author:app/example-app-dev": 30},
                       "other/lib": {"is:pr is:merged": 0, "is:issue is:closed": 0, "author:app/example-lib-dev": 0},
                       "example/notes": {"is:pr is:merged": 1, "is:issue is:closed": 0}}
        self.timers = {"bot-round@app-dev.timer": "active", "bot-review@app-plan.timer": "inactive", "bot-round@lib-dev.timer": "active"}

    def __call__(self, cmd, timeout=60, env=None):
        self.calls.append((cmd, env))
        name = os.path.basename(cmd[0])
        if name == "fleet-token":
            app = cmd[cmd.index("--app") + 1] if "--app" in cmd else None
            return self.tokens[app] + "\n", None
        if name == "systemctl":
            unit = cmd[2] if cmd[1] == "show" else cmd[-1]
            if cmd[1] == "show":
                return f"LoadState={'loaded' if unit in self.timers else 'not-found'}\nActiveState={self.timers.get(unit, 'inactive')}\n", None
            return json.dumps([{"next": 1_800_000_000_000_000, "last": 1_799_999_000_000_000}]), None
        if name == "nvidia-smi":
            return None, "no gpu"
        assert name == "gh", cmd
        assert env and env.get("GH_TOKEN"), "gh without a token"
        repo = cmd[cmd.index("-R") + 1] if "-R" in cmd else None
        if cmd[1] == "api":
            path = cmd[2]
            if path.startswith("repos/"):
                repo, sha = path.split("/")[1] + "/" + path.split("/")[2], path.split("/")[4].split("?")[0]
                if repo in self.fail:
                    return None, "boom"
                return json.dumps(self.checks.get(sha, [])), None
            q = cmd[cmd.index("-f") + 1][2:]   # q=repo:... is:pr ...
            repo = q.split()[0][5:]
            if repo in self.fail:
                return None, "search boom"
            key = max((k for k in self.totals[repo] if k in q), key=len)   # the author query also holds "is:pr is:merged"
            return str(self.totals[repo][key]), None
        if repo in self.fail:
            return None, f"GraphQL: Could not resolve to a Repository with the name '{repo}'"
        if env.get("GH_TOKEN") != ("tok-lib" if repo.startswith("other/") else "tok-default"):
            return None, "wrong App for this owner"
        if cmd[1] == "pr" and "--state" in cmd and cmd[cmd.index("--state") + 1] == "open":
            return json.dumps(self.prs[repo]), None
        if cmd[1] == "pr":
            return json.dumps(self.merged[repo]), None
        if cmd[1] == "issue" and cmd[cmd.index("--state") + 1] == "open":
            return json.dumps(self.issues[repo]), None
        return json.dumps(self.closed[repo]), None


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.agents = self.root / "agents"
        (self.agents / "status").mkdir(parents=True)
        (self.agents / "reports" / "box").mkdir(parents=True)
        (self.agents / "reports" / "box" / "latest.txt").write_text("box ok 12:00\nWARN something\nCRIT bad thing\n")
        (self.agents / "status" / "app-dev-round.json").write_text(json.dumps({"started": "x", "ended": "y", "outcome": "built", "what": "#10", "cost_usd": 0.4, "turns": 9, "signed_in": True, "inbox": 0}))
        (self.agents / "status" / "app-dev-round.log").write_text(
            "\n".join([f"{YESTERDAY}T10:00:00Z built 0.50 10 #8", f"{YESTERDAY}T11:00:00Z idle 0.01 1 -", f"{TODAY}T09:00:00Z built 0.40 9 #10", f"{TODAY}T09:30:00Z stuck 0.90 150 #11"]) + "\n")
        (self.agents / "status" / "app-plan-review.json").write_text(json.dumps({"outcome": "reviewed", "signed_in": False}))
        self.doc = tomllib.loads(FLEET.format(agents=self.agents))
        self.out = self.root / "page" / "fleet.json"
        self.cfg = collect.config(self.doc, machine="box", out=self.out, fleet_token="/x/fleet-token")
        self.cfg.gh = "/x/gh"
        self.run = FakeRun()

    def collect(self):
        c = collect.Collector(self.cfg, self.run)
        data = c.collect()
        collect.write_json(self.out, data, mode=0o644)
        return data, c

    def test_config_from_file(self):
        cfg = self.cfg
        self.assertEqual((cfg.owner, cfg.guard, cfg.home, cfg.machine), ("example", "box-guard", "/srv/box-guard", "box"))
        self.assertEqual(cfg.labels, {"ready": "ready", "next": "next", "decide": "needs-owner", "live": "live"})
        self.assertEqual(cfg.owner_app, {"other": "lib"})
        self.assertEqual([(r, n) for r, n in cfg.projects], [("example/app", "app"), ("other/lib", "lib"), ("example/notes", "notes")])
        names = [(b["name"], b["kind"], b["project"], b["rounds"]) for b in cfg.bots]
        self.assertEqual(names, [("app-dev", "dev", "app", "round"), ("app-plan", "plan", "app", "review"), ("lib-dev", "dev", "lib", "round"),
                                 ("box-guard", "guard", "notes", "none"), ("lap-guard", "guard", "notes", "none")])
        self.assertEqual(cfg.bots[0]["app"], "example-app-dev")
        self.assertEqual(cfg.knobs, {"fantasy": {"budget_dev_usd": 3}})
        self.assertEqual((cfg.page_set, cfg.page_sets), ("fantasy", []))
        self.assertEqual(cfg.state_dir, self.root / "page" / "state")
        _, doc = model.load(MINIMAL)
        m = collect.config(doc, machine="box", out=self.out)
        self.assertEqual((m.guard, m.agents_dir, m.page_set, m.disks, m.gpu), ("example", pathlib.Path("/srv/agents"), "default", ["/"], False))
        with self.assertRaises(ValueError):
            collect.config(doc, machine="nowhere")

    def test_shared_part(self):
        data, c = self.collect()
        self.assertEqual([p["name"] for p in data["projects"]], ["app", "lib", "notes"])
        app = data["projects"][0]
        self.assertEqual([(pr["number"], pr["review"], pr["checks"]) for pr in app["prs"]],
                         [(1, "approved", "green"), (2, "none", "red"), (3, "none", "green"), (4, "none", "none")])
        self.assertEqual([i["number"] for i in app["issues"]], [12, 10, 11])   # decide first, the plain one left out
        self.assertNotIn("error", data["projects"][1])   # the second owner's App was used
        lib_calls = [cmd for cmd, env in self.run.calls if "-R" in cmd and cmd[cmd.index("-R") + 1] == "other/lib"]
        self.assertTrue(lib_calls)
        self.assertEqual(data["projects"][1]["prs"][0]["review"], "none")   # a COMMENTED review decides nothing
        bots = {b["name"]: b for b in data["bots"]}
        self.assertEqual(bots["app-dev"]["last"]["outcome"], "built")
        self.assertEqual((bots["app-dev"]["cost_today"], bots["app-dev"]["rounds_today"]), (1.3, 2))
        self.assertTrue(bots["app-dev"]["timer"]["active"])
        self.assertFalse(bots["app-plan"]["timer"]["active"])
        self.assertIsNone(bots["lib-dev"]["last"])   # no status file yet
        self.assertNotIn("last", bots["lap-guard"])   # another machine's: comes with its report
        self.assertEqual([g["host"] for g in data["guards"]], ["box", "laptop"])
        self.assertEqual(data["guards"][0]["findings"], ["WARN something", "CRIT bad thing"])
        self.assertIn("error", data["guards"][1])
        self.assertEqual(data["machine"]["name"], "box")
        self.assertIn("/", data["machine"]["disks"])
        self.assertEqual(data["machine"]["gpu"], {})   # gpu = false: nvidia-smi never asked
        self.assertFalse([cmd for cmd, _ in self.run.calls if "nvidia-smi" in cmd[0]])
        h = data["history"]
        self.assertEqual(len(h["days"]), collect.HISTORY_DAYS)
        self.assertEqual((h["projects"]["app"]["merged"][-1], h["projects"]["app"]["merged"][-2]), (1, 1))
        self.assertEqual(h["projects"]["app"]["merged_by"]["app/example-app-dev"][-1], 1)
        self.assertEqual((h["projects"]["app"]["merged_total"], h["projects"]["app"]["closed_total"]), (42, 7))
        self.assertEqual(h["bots"]["app-dev"]["rounds"][-1], 2)
        self.assertEqual((h["bots"]["app-dev"]["done_total"], h["bots"]["app-dev"]["bad_total"], h["bots"]["app-dev"]["merged_total"]), (2, 1, 30))
        self.assertNotIn("lap-guard", h["bots"])
        self.assertEqual(data["page"]["set"], "fantasy")
        self.assertEqual(data["page"]["sets"], ["default", "minimal", "fantasy"])
        self.assertEqual(data["page"]["personas"], {"app-plan": "Ada"})
        self.assertEqual(data["page"]["labels"]["decide"], "needs-owner")
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o644)
        self.assertIn("GitHub calls", c.summary(data, 1.0))

    def test_items(self):
        data, _ = self.collect()
        by = {i["id"]: i for i in data["items"]}
        self.assertIn("merge:example/app#1", by)
        self.assertEqual(by["merge:example/app#1"]["why"], "approved by app-plan, CI green")
        self.assertIn("merge:other/lib#7", by)   # no reviewing plan bot there: review & merge
        self.assertEqual(by["merge:other/lib#7"]["tag"], "review & merge")
        self.assertEqual(by["merge:other/lib#7"]["why"], "by lib-dev, no CI here")
        self.assertIn("step:ci:example/app#2", by)   # red and not a dev bot's
        self.assertIn("decide:example/app#2", by)    # the decide label on a pull request
        self.assertIn("decide:example/app#12", by)
        self.assertIn("step:live:example/app#11", by)
        self.assertNotIn("merge:example/app#3", by)  # drafts never
        self.assertIn("step:signin:app-plan", by)
        self.assertEqual(by["step:timer:app-plan"]["why"], "1 pull request waits for its review")   # #4 unreviewed, timer off
        self.assertNotIn("step:timer:app-dev", by)   # its timer is on
        self.assertEqual(by["step:crit:box"]["text"], "box: 1 critical finding")
        self.assertIn("step:guard:laptop", by)
        self.assertTrue(all(i["since"] for i in data["items"]))

    def test_since_survives_runs_and_hiccups(self):
        data1, _ = self.collect()
        since = {i["id"]: i["since"] for i in data1["items"]}
        self.run.fail.add("example/app")
        data2, _ = self.collect()
        ids2 = {i["id"] for i in data2["items"]}
        self.assertIn("step:error:example/app", ids2)
        self.assertNotIn("merge:example/app#1", ids2)
        self.assertTrue(data2["history"]["projects"]["app"].get("stale"))   # the window kept within the day
        self.run.fail.clear()
        data3, _ = self.collect()
        since3 = {i["id"]: i["since"] for i in data3["items"]}
        self.assertEqual(since3["merge:example/app#1"], since["merge:example/app#1"])   # kept through the hiccup
        state = json.loads((self.cfg.state_dir / "items.json").read_text())
        self.assertNotIn("step:error:example/app", state)

    def test_totals_cached_within_the_hour(self):
        self.collect()
        n1 = sum(1 for cmd, _ in self.run.calls if "search/issues" in cmd)
        self.collect()
        n2 = sum(1 for cmd, _ in self.run.calls if "search/issues" in cmd)
        self.assertEqual(n1, 9)       # 2 per project + 1 per local bot with rounds (app-dev, app-plan, lib-dev)
        self.assertEqual(n2, n1)      # nothing changed: no new searches
        self.run.merged["example/app"].append({"mergedAt": iso(datetime.now(timezone.utc)), "author": {"login": "x"}})
        self.collect()
        n3 = sum(1 for cmd, _ in self.run.calls if "search/issues" in cmd)
        self.assertEqual(n3, n2 + 4)  # app's two counts and its two bots'


    def test_fantasy_block(self):
        data, _ = self.collect()
        f = data["sets"]["fantasy"]
        self.assertNotIn("error", f)
        q = f["quest"]
        self.assertEqual((q["day"], q["open"], q["max"], q["streak"], q["cleared_today"]), (TODAY, len(data["items"]), len(data["items"]), 0, False))
        self.assertEqual(q["done"], [])
        self.assertEqual(f["budgets"], {"dev": 3.0, "plan": 1.0})
        lv = f["levels"]
        # app: 42 merged, 7 closed, app-dev's 2 done rounds -> 420 + 21 + 4
        self.assertEqual((lv["projects"]["app"]["xp"], lv["projects"]["app"]["lv"]), (445, 4))
        self.assertEqual({b["name"]: b["r"] for b in lv["projects"]["app"]["badges"]}, {"merged": 1, "streak": 0, "big day": -1, "clean": 0, "steady": 0})   # today has a bad round: yesterday was clean
        self.assertEqual(lv["projects"]["app"]["badges"][0]["why"], "42; silver at 50")
        self.assertEqual((lv["bots"]["app-dev"]["xp"], lv["bots"]["app-dev"]["merged"]), (304, 30))
        frugal = next(b for b in lv["bots"]["app-dev"]["badges"] if b["name"] == "frugal")
        self.assertEqual((frugal["r"], frugal["why"]), (-1, "fewer than 20 rounds in 30 days; wood at $0.50 per round, 30 days"))
        self.assertEqual(f["fleet_streak"]["n"], 2)
        self.assertTrue(f["fleet_streak"]["today_has"])
        self.assertTrue((self.cfg.state_dir / "fantasy.json").is_file())
        # an item goes: the done list says what it was; a day without items clears
        self.run.prs["example/app"] = [pr for pr in self.run.prs["example/app"] if pr["number"] != 1]
        data2, _ = self.collect()
        q2 = data2["sets"]["fantasy"]["quest"]
        self.assertEqual([d["id"] for d in q2["done"]], ["merge:example/app#1"])
        self.assertEqual(q2["done"][0]["text"], "approved and green")
        self.assertEqual(q2["max"], q["max"])
        self.assertEqual(q2["open"], q["open"] - 1)

    def test_fantasy_levels_count_botless_project(self):
        data, _ = self.collect()
        self.assertEqual(data["sets"]["fantasy"]["levels"]["projects"]["notes"]["xp"], 10)

    def test_fantasy_takes_old_quest_file_once(self):
        old = {"day": TODAY, "max": 99, "cleared": [YESTERDAY], "seen": {"merge:example/app#1": "2020-01-01T00:00:00+00:00"}, "done": {}, "days": {}}
        self.out.parent.mkdir(parents=True)
        (self.out.with_name("quest.json")).write_text(json.dumps(old))
        data, _ = self.collect()
        q = data["sets"]["fantasy"]["quest"]
        self.assertEqual((q["max"], q["streak"]), (99, 1))
        since = {i["id"]: i["since"] for i in data["items"]}
        self.assertEqual(since["merge:example/app#1"], "2020-01-01T00:00:00+00:00")
        self.assertNotIn("seen", json.loads((self.cfg.state_dir / "fantasy.json").read_text()))

    def test_own_sets(self):
        d = self.cfg.assets / "sets"
        d.mkdir(parents=True)
        (d / "mine.py").write_text(textwrap.dedent("""
            def collect(shared, state, ctx):
                n = state.get("runs", 0) + 1
                return {"runs": n, "items": len(shared["items"]), "knob": ctx["knobs"].get("k"), "owner": ctx["owner"]}, {"runs": n}
        """))
        (d / "broken.py").write_text("def collect(shared, state, ctx):\n    return 1 / 0\n")
        (d / "slow.py").write_text("import time\ndef collect(shared, state, ctx):\n    time.sleep(5)\n    return {}, {}\n")
        (d / "jsonly.js").write_text("fleet.set('jsonly', {})\n")
        (d / "wrong.py").write_text("def collect(shared, state, ctx):\n    return {'a': 1}\n")
        self.doc["page"]["mine"] = {"k": "v"}
        self.doc["page"]["sets"] = ["default", "fantasy", "mine", "broken", "jsonly", "wrong", "missing", "slow"]
        self.cfg = collect.config(self.doc, machine="box", out=self.out, fleet_token="/x/fleet-token")
        self.cfg.gh = "/x/gh"
        sets.TIMEOUT, saved = 1, sets.TIMEOUT
        self.addCleanup(setattr, sets, "TIMEOUT", saved)
        data, _ = self.collect()
        self.assertEqual(data["page"]["sets"], self.doc["page"]["sets"])
        s = data["sets"]
        self.assertEqual(s["mine"], {"runs": 1, "items": len(data["items"]), "knob": "v", "owner": "example"})
        self.assertIn("ZeroDivisionError", s["broken"]["error"])
        self.assertNotIn("jsonly", s)                  # a page half alone is fine
        self.assertNotIn("default", s)
        self.assertIn("two dicts", s["wrong"]["error"])
        self.assertIn("no such set", s["missing"]["error"])
        self.assertIn("no answer after 1 s", s["slow"]["error"])
        self.assertNotIn("error", s["fantasy"])
        data2, _ = self.collect()
        self.assertEqual(data2["sets"]["mine"]["runs"], 2)   # its state came back
        # without page.sets the own ones are found next to the kit's
        self.doc["page"].pop("sets")
        cfg = collect.config(self.doc, machine="box", out=self.out)
        self.assertEqual(sets.names(cfg.page_sets, cfg.assets), ["default", "minimal", "fantasy", "broken", "jsonly", "mine", "slow", "wrong"])

    def test_who_and_review_state(self):
        c = collect.Collector(self.cfg, self.run)
        self.assertEqual(c.who("app/example-app-dev"), "app-dev")
        self.assertEqual(c.who("app/other-bot"), "other-bot")
        self.assertEqual(c.who("someone"), "someone")
        reviews = [{"author": {"login": "app/example-app-plan"}, "state": "APPROVED", "submittedAt": "2026-01-01T00:00:00Z"},
                   {"author": {"login": "someone"}, "state": "CHANGES_REQUESTED", "submittedAt": "2026-01-02T00:00:00Z"},
                   {"author": {"login": "someone"}, "state": "APPROVED", "submittedAt": "2026-01-03T00:00:00Z"}]
        self.assertEqual(c.review_state({"reviewDecision": "", "author": {"login": "app/example-app-dev"}, "reviews": reviews}), "approved")
        self.assertEqual(c.review_state({"reviewDecision": "", "author": {"login": "app/example-app-dev"}, "reviews": reviews[:2]}), "changes_requested")
        self.assertEqual(c.review_state({"reviewDecision": "REVIEW_REQUIRED", "reviews": reviews}), "review_required")
        self.assertEqual(c.review_state({"reviewDecision": "", "author": {"login": "someone"}, "reviews": reviews[1:]}), "none")

    def test_no_token_at_all(self):
        self.run.tokens = {}
        self.run.__call__  # noqa
        orig = self.run.__class__.__call__

        def no_token(self_, cmd, timeout=60, env=None):
            if os.path.basename(cmd[0]) == "fleet-token":
                return None, "no key"
            return orig(self_, cmd, timeout, env)
        self.run.__class__ = type("NoTokenRun", (FakeRun,), {"__call__": no_token})
        data, _ = self.collect()
        self.assertEqual(data["error"], "fleet-token: no token for any owner")
        self.assertTrue(all(p["error"].startswith("no token") for p in data["projects"]))
        self.assertEqual(sorted(i["id"] for i in data["items"] if i["kind"] == "step" and i["tag"] == "error"),
                         ["step:error:example/app", "step:error:example/notes", "step:error:other/lib"])

    def test_cli_end_to_end_with_fake_binaries(self):
        binr = self.root / "bin"
        binr.mkdir()
        for name, body in (("gh", 'case "$*" in *check-runs*) echo "[]";; *search/issues*) echo 3;; *) echo "[]";; esac\n'),
                           ("fleet-token", 'echo tok\n')):
            f = binr / name
            f.write_text("#!/bin/sh\n" + body)
            f.chmod(0o755)
        toml = self.root / "fleet.toml"
        toml.write_text(FLEET.format(agents=self.agents))
        rc = cli.main(["collect", str(toml), "--out", str(self.out), "--machine", "box", "--fleet-token", str(binr / "fleet-token"), "--gh", str(binr / "gh")])
        self.assertEqual(rc, 0)
        data = json.loads(self.out.read_text())
        self.assertEqual(data["history"]["projects"]["app"]["merged_total"], 3)
        self.assertIn("fantasy", data["sets"])
        self.assertEqual(cli.main(["collect", str(toml), "--machine", "nowhere"]), 1)


class CheckPageTests(unittest.TestCase):
    def check(self, extra):
        text = MINIMAL.read_text() + "\n" + extra
        return [str(x) for x in model.check(text, tomllib.loads(text))]

    def test_page_table(self):
        self.assertEqual(self.check('[page]\nset = "minimal"\nsets = ["minimal", "mine"]\ncollect_every = "5m"\n[page.labels]\ndecide = "needs-us"\n[page.mine]\nk = 1\n'), [])
        f = self.check('[page]\nset = "fantasy"\nsets = ["default"]\ncollect_every = "soon"\n[page.labels]\nwhat = "x"\n[page.other]\nk = 1\n')
        self.assertIn("error: page.set: `fantasy` is not in page.sets", f)
        self.assertIn("error: page.collect_every: a systemd span like 5m or 1h", f)
        self.assertIn("warn: page.labels: unknown key `what`", f)
        self.assertIn("warn: page.other: knobs for a set that is neither the kit's nor in page.sets", f)
        self.assertIn("error: page.sets: must be a list of set names (letters, digits, - and _)", self.check('[page]\nsets = ["a b"]\n'))
        self.assertIn("warn: defaults.page: moved to [page] (collect_every) and [page.fantasy] (the budgets); ignored", self.check('[defaults.page]\nbudget_dev_usd = 2\n'))

    def test_machine_disks_and_gpu(self):
        self.assertEqual(self.check('[machines.other]\nrole = "guard"\nfamily = "arch"\nhome_root = "/srv"\ndisks = ["/", "/data"]\ngpu = { lock = "/srv/gpu/lock" }\n'), [])
        f = self.check('[machines.other]\nrole = "guard"\nfamily = "arch"\nhome_root = "/srv"\ndisks = ["data"]\ngpu = "yes"\n')
        self.assertIn("error: machines.other.disks: a list of absolute paths", f)
        self.assertTrue(any(x.startswith("error: machines.other.gpu") for x in f))


class FantasyUnitTests(unittest.TestCase):
    def test_rank_and_days(self):
        self.assertEqual(fantasy.rank("merged", 42, [1, 10, 50, 100, 250, 500]), {"name": "merged", "r": 1, "why": "42; silver at 50"})
        self.assertEqual(fantasy.rank("merged", 600, [1, 10, 50, 100, 250, 500])["why"], "600; top rank")
        self.assertEqual(fantasy.rank("frugal", 0.3, [0.5, 0.25, 0.1], fantasy.money, lower=True), {"name": "frugal", "r": 0, "why": "$0.30; bronze at $0.25"})
        self.assertEqual(fantasy.days_in_row([1, 1, 0, 1, 1], lambda v: v > 0), 2)
        self.assertEqual(fantasy.days_in_row([1, 1, 0, 1, 0], lambda v: v > 0), 1)   # today empty: yesterday counts
        self.assertEqual(fantasy.days_in_row([], lambda v: v > 0), 0)
        self.assertEqual(fantasy.level(445), 4)
        self.assertEqual(fantasy.level(0), 0)


if __name__ == "__main__":
    unittest.main()
