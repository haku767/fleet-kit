import json
import pathlib
import tempfile
import tomllib
import unittest

from fleet_kit import accounts, model, render, rounds

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"
SOLO = HERE.parent / "examples" / "solo.toml"


def findings(text):
    return [str(x) for x in model.check(text, tomllib.loads(text))]


def errors(text):
    return [x for x in findings(text) if x.startswith("error")]


class KindTests(unittest.TestCase):
    def test_solo_example_is_clean(self):
        text, doc = model.load(SOLO)
        self.assertEqual(findings(text), [])
        self.assertEqual(model.kind_of(doc["projects"]["notes"]), "session")
        self.assertEqual(model.kind_of(doc["projects"]["myapp"]), "solo")
        self.assertEqual(model.reviewer(doc).account, "fleet-review")

    def test_kind_is_derived_from_the_bots(self):
        _, doc = model.load(MINIMAL)
        self.assertEqual(model.kind_of(doc["projects"]["myapp"]), "pair")
        self.assertIsNone(model.reviewer(doc))

    def test_kind_must_match_the_bots(self):
        base = MINIMAL.read_text()
        e = errors(base.replace('repo = "example/myapp"', 'repo = "example/myapp"\nkind = "solo"'))
        self.assertTrue(any("kind solo has a dev bot and no plan bot" in x for x in e), e)
        e = errors(base.replace('repo = "example/myapp"', 'repo = "example/myapp"\nkind = "session"'))
        self.assertTrue(any("kind session has no bots" in x for x in e), e)
        e = errors(base.replace('repo = "example/myapp"', 'repo = "example/myapp"\nkind = "duo"'))
        self.assertTrue(any("kind must be one of" in x for x in e), e)

    def test_dev_without_plan_or_reviewer_warns(self):
        text = MINIMAL.read_text().replace('plan = { name = "myapp-plan" }\n', "")
        f = findings(text)
        self.assertTrue(any("nobody reviews" in x for x in f), f)
        self.assertFalse(any("usually has both" in x for x in f), f)

    def test_reviewer_and_uid_checks(self):
        base = SOLO.read_text()
        e = errors(base.replace('machine = "box", persona', 'machine = "nowhere", persona'))
        self.assertTrue(any("fleet.reviewer" in x and "machine" in x for x in e), e)
        e = errors(base.replace("uid = 1201", "uid = 1200"))
        self.assertTrue(any("uid 1200 is" in x for x in e), e)
        e = errors(base.replace("uid = 1201", "uid = 12"))
        self.assertTrue(any("uid must be a whole number from 1000" in x for x in e), e)
        e = errors(base.replace('dev = "example-fleet-dev"', 'dev = "not a slug"'))
        self.assertTrue(any("apps.dev" in x for x in e), e)


class RenderTests(unittest.TestCase):
    def setUp(self):
        _, self.doc = model.load(SOLO)
        self.files = render.render(self.doc)

    def test_reviewer_conf_per_project(self):
        conf = self.files["bots/fleet-review.myapp.conf"]
        self.assertIn("kind='review'", conf)
        self.assertIn("project='myapp'", conf)
        self.assertIn("app='example-fleet-dev'", conf)              # the dev bot's pull requests
        self.assertIn("reviewer='example-fleet-review[bot]'", conf)
        self.assertNotIn("bots/fleet-review.notes.conf", self.files)   # no dev bot, nothing to review

    def test_fleet_apps_serve_the_bots(self):
        dev = self.files["bots/myapp-dev.conf"]
        self.assertIn("app='example-fleet-dev'", dev)
        self.assertIn("trusted='example example-fleet-review[bot]'", dev)

    def test_lists_and_tables_carry_the_reviewer(self):
        lists = self.files["machines/box/lists.sh"]
        self.assertIn("reviewer='fleet-review'", lists)
        self.assertIn("reviewer_projects='myapp'", lists)
        self.assertIn("bots='myapp-dev fleet-review'", lists)
        tables = json.loads(self.files["tables.json"])
        self.assertIn(["fleet-review", "review", "", "box", "review", "fleet-review"], tables["bots"])
        self.assertIn("| myapp | solo |", self.files["rules-table.md"])

    def test_fleet_json_for_a_declarative_machine(self):
        fj = json.loads(self.files["machines/box/fleet.json"])
        self.assertEqual(fj["family"], "nixos")
        self.assertEqual(fj["apps"], {"dev": "example-fleet-dev", "review": "example-fleet-review"})
        by = {a["account"]: a for a in fj["accounts"]}
        self.assertEqual(by["myapp-dev"]["uid"], 1201)
        self.assertEqual(by["fleet-review"]["kind"], "review")
        self.assertEqual(by["fleet-review"]["uid"], 1200)
        self.assertEqual(by["erik"]["kind"], "agent")
        self.assertFalse(by["erik"]["linger"])
        self.assertEqual([t["unit"] for t in fj["timers"]], ["bot-round@myapp-dev.timer", "bot-review-for@myapp.timer"])

    def test_apply_lists_leaves_fleet_json_alone(self):
        with tempfile.TemporaryDirectory() as d:
            acts = render.apply_lists(self.files, "box", d)
            self.assertEqual(sorted(p.rsplit("/", 1)[1] for _, p in acts), ["lists.sh"])


class FakeSystemctl:
    def __init__(self):
        self.calls, self.state = [], {}

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "show":
            st = self.state.get(args[1], ("disabled", "inactive"))
            return f"UnitFileState={st[0]}\nActiveState={st[1]}\n"
        if args[0] == "enable":
            self.state[args[1]] = ("enabled", "inactive")
        if args[0] == "disable":
            self.state[args[-1]] = ("disabled", "inactive")
        return ""


class ReviewerRoundsTests(unittest.TestCase):
    def test_reviewer_units_and_timers(self):
        _, doc = model.load(SOLO)
        doc["machines"]["box"]["family"] = "arch"   # apply rounds does nothing for nixos
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "srv/agents").mkdir(parents=True)
            sc = FakeSystemctl()
            acts = rounds.apply(doc, "box", root=root, systemctl=sc)
            unit = (root / "etc/systemd/system/bot-review-for@.service").read_text()
            self.assertIn("User=fleet-review", unit)
            self.assertIn("ExecStart=/usr/local/bin/bot-review --project %i", unit)
            self.assertNotIn("{reviewer}", unit)
            self.assertIn(("enabled", "bot-review-for@myapp.timer"), acts)
            self.assertTrue((root / "srv/fleet-review/review").is_dir())
            self.assertIn(["enable", "bot-review-for@myapp.timer"], sc.calls)

    def test_reviewer_gets_an_account_and_inbox(self):
        _, doc = model.load(SOLO)
        doc["machines"]["box"]["family"] = "arch"
        calls = []

        def run(argv):
            calls.append(argv)
            return ""
        with tempfile.TemporaryDirectory() as d:
            acts = accounts.apply_accounts(doc, "box", root=d, run=run, user_exists=lambda u: False, group_exists=lambda g: True)
        self.assertTrue(any("account fleet-review" in w for _, w in acts), acts)
        self.assertTrue(any(c[0] == "useradd" and c[-1] == "fleet-review" for c in calls), calls)
        add = next(c for c in calls if c[0] == "useradd" and c[-1] == "fleet-review")
        self.assertIn("--uid", add)
        self.assertEqual(add[add.index("--uid") + 1], "1200")   # the uid the file pins
        add = next(c for c in calls if c[0] == "useradd" and c[-1] == "myapp-dev")
        self.assertEqual(add[add.index("--uid") + 1], "1201")
        self.assertEqual(accounts.inbox_owner(doc, render.Fleet(doc), "fleet-review", "box", {"fleet-review"}), "fleet-review")


if __name__ == "__main__":
    unittest.main()


class AddProjectKindTests(unittest.TestCase):
    def run_cli(self, argv):
        import contextlib
        import io
        from fleet_kit import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_kind_solo_then_a_plan_bot_makes_a_pair(self):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "fleet.toml"
            shutil.copy(MINIMAL, f)
            rc, out, err = self.run_cli(["add-project", "tool", "--repo", "example/tool", "--stack", "python-uv", "--kind", "solo", str(f)])
            self.assertEqual(rc, 0, err)
            self.assertIn('kind = "solo"', f.read_text())
            self.assertIn("bots tool-dev)", out)
            rc, out, err = self.run_cli(["add-bot", "tool", "plan", str(f)])
            self.assertEqual(rc, 0, err)
            text = f.read_text()
            self.assertIn('kind = "pair"', text)
            self.assertNotIn('kind = "solo"', text)
            rc, out, err = self.run_cli(["add-project", "notes", "--repo", "example/notes", "--kind", "session", str(f)])
            self.assertEqual(rc, 0, err)
            self.assertIn("no bots)", out)
            _, doc = model.load(f)
            self.assertEqual(model.kind_of(doc["projects"]["notes"]), "session")
