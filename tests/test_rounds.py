import pathlib
import subprocess
import tempfile
import tomllib
import unittest

from fleet_kit import model, render, rounds

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"


class FakeSystemctl:
    def __init__(self):
        self.calls = []
        self.state = {}   # unit -> (UnitFileState, ActiveState)

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "show":
            st = self.state.get(args[1], ("disabled", "inactive"))
            return f"UnitFileState={st[0]}\nActiveState={st[1]}\n"
        if args[0] == "enable":
            self.state[args[1]] = ("enabled", self.state.get(args[1], ("", "inactive"))[1])
        if args[0] == "start":
            self.state[args[1]] = (self.state.get(args[1], ("disabled", ""))[0], "active")
        if args[0] == "disable":
            self.state[args[-1]] = ("disabled", "inactive")
        return ""


def load():
    _, doc = model.load(MINIMAL)
    return doc


class RoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "srv/agents").mkdir(parents=True)
        self.sc = FakeSystemctl()

    def apply(self, doc, **kw):
        return rounds.apply(doc, "box", root=self.root, systemctl=self.sc, **kw)

    def test_installs_and_enables(self):
        acts = self.apply(load())
        kinds = {w for w, _ in acts}
        self.assertNotIn("missing", kinds)
        for s in rounds.SCRIPTS:
            p = self.root / "usr/local/bin" / s
            self.assertTrue(p.is_file(), s)
            self.assertEqual(p.stat().st_mode & 0o777, 0o755)
        unit = (self.root / "etc/systemd/system/bot-round@.service").read_text()
        self.assertIn("ConditionPathExists=/srv/%i/.local/bin/claude", unit)
        self.assertNotIn("{home_root}", unit)
        self.assertEqual((self.root / "etc/systemd/system/bot-round@.timer").stat().st_mode & 0o777, 0o644)
        self.assertTrue((self.root / "srv/agents/status").is_dir())
        self.assertEqual((self.root / "srv/agents/status").stat().st_mode & 0o7777, 0o2775)
        for d in ("srv/myapp-dev/work", "srv/myapp-dev/log", "srv/myapp-plan/review", "srv/myapp-plan/log"):
            self.assertTrue((self.root / d).is_dir(), d)
            self.assertEqual((self.root / d).stat().st_mode & 0o777, 0o750)
        self.assertIn(["daemon-reload"], self.sc.calls)
        self.assertIn(["enable", "bot-round@myapp-dev.timer"], self.sc.calls)
        self.assertIn(["enable", "bot-review@myapp-plan.timer"], self.sc.calls)
        self.assertNotIn(["start", "bot-round@myapp-dev.timer"], self.sc.calls)
        self.assertTrue(any(w == "kept" and "not started" in x for w, x in acts))

    def test_second_run_changes_nothing_and_start(self):
        self.apply(load())
        acts = self.apply(load())
        self.assertEqual({w for w, _ in acts}, {"kept"})
        acts = self.apply(load(), start=True)
        self.assertIn(("started", "bot-round@myapp-dev.timer"), acts)
        self.assertIn(["start", "bot-review@myapp-plan.timer"], self.sc.calls)

    def test_home_root_in_units(self):
        doc = load()
        doc["machines"]["box"]["home_root"] = "/home/bots"
        self.apply(doc)
        unit = (self.root / "etc/systemd/system/bot-review@.service").read_text()
        self.assertIn("WorkingDirectory=/home/bots/%i", unit)
        self.assertTrue((self.root / "home/bots/myapp-plan/review").is_dir())

    def test_paused_disables(self):
        doc = load()
        doc["projects"]["myapp"]["paused"] = True
        self.sc.state["bot-round@myapp-dev.timer"] = ("enabled", "active")
        acts = self.apply(doc)
        self.assertIn(["disable", "--now", "bot-round@myapp-dev.timer"], self.sc.calls)
        self.assertTrue(any(w == "disabled" for w, _ in acts))
        self.assertNotIn(["enable", "bot-round@myapp-dev.timer"], self.sc.calls)
        acts = self.apply(doc)
        self.assertTrue(any(w == "kept" and "paused" in x for w, x in acts))

    def test_dry_run_writes_nothing_but_reads_the_units(self):
        self.sc.state["bot-round@myapp-dev.timer"] = ("enabled", "active")
        acts = self.apply(load(), dry_run=True)
        self.assertFalse((self.root / "usr/local/bin/bot-round").exists())
        self.assertEqual({c[0] for c in self.sc.calls}, {"show"})   # read-only: no reload, enable or start
        self.assertIn(("added", "/usr/local/bin/bot-round"), acts)
        self.assertIn(("kept", "bot-round@myapp-dev.timer enabled"), acts)       # the real state, not "would be enabled"
        self.assertIn(("enabled", "bot-review@myapp-plan.timer"), acts)

    def test_trial_root_never_calls_the_real_systemctl(self):
        acts = rounds.apply(load(), "box", root=self.root)   # no fake: the real one must not run
        self.assertTrue(any(w == "skipped" and "trial root" in x for w, x in acts))
        self.assertIn(("enabled", "bot-round@myapp-dev.timer"), acts)   # reported, not done
        self.assertTrue((self.root / "usr/local/bin/bot-review").is_file())

    def test_ensure_dir_reports_changed_mode(self):
        (self.root / "srv/myapp-dev/work").mkdir(parents=True)
        (self.root / "srv/myapp-dev/work").chmod(0o700)
        acts = self.apply(load())
        self.assertIn(("changed", "/srv/myapp-dev/work"), acts)
        self.assertIn(("added", "/srv/myapp-dev/log"), acts)

    def test_nixos_and_other_machines(self):
        doc = load()
        doc["machines"]["box"]["family"] = "nixos"
        self.assertEqual(self.apply(doc)[0][0], "skipped")
        self.assertEqual(rounds.apply(load(), "elsewhere", root=self.root, systemctl=self.sc)[0][0], "skipped")

    def test_shipped_scripts_read_the_conf(self):
        """bot-round and bot-review source the rendered .conf: the knobs arrive as variables."""
        files = render.render(load())
        dev = files["bots/myapp-dev.conf"]
        for k in ("owner='ErikNeu777'", "guard=''", "agents_dir='/srv/agents'", "max_worktrees='2'", "model_small='claude-sonnet-5-5'"):
            self.assertIn(k, dev)
        plan = files["bots/myapp-plan.conf"]
        self.assertIn("max_per_pr='3'", plan)
        # the scripts' own defaults block, run against the file
        script = rounds.data("bot-round")
        block = script[script.index("owner=${owner:-"):script.index("model_s=")] + script[script.index("model_s="):].split("\n", 1)[0]
        r = subprocess.run(["sh", "-c", dev + block + '\nprintf "%s %s %s %s" "$owner" "$max" "$budget" "$model_m"'],
                           capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(r.stdout, "ErikNeu777 2 10 claude-opus-5-5")
        r = subprocess.run(["sh", "-c", dev + block + '\nprintf "%s" "$max"'],
                           capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "BOT_ROUND_MAX": "5"})
        self.assertEqual(r.stdout, "5")   # /etc/default/bot-round still wins
        for s in ("bot-round", "bot-review"):
            self.assertEqual(subprocess.run(["sh", "-n"], input=rounds.data(s), capture_output=True, text=True).returncode, 0, s)

    def test_defaults_tables_and_bot_budget(self):
        doc = load()
        doc["defaults"] = {"round": {"max_worktrees": 3, "model_large": "x-large"}, "review": {"max_per_pr": 2}}
        doc["projects"]["myapp"]["dev"]["budget_usd"] = 4
        files = render.render(doc)
        dev = files["bots/myapp-dev.conf"]
        self.assertIn("max_worktrees='3'", dev)
        self.assertIn("model_large='x-large'", dev)
        self.assertIn("budget_usd='4'", dev)
        self.assertIn("max_turns='150'", dev)
        self.assertIn("max_per_pr='2'", files["bots/myapp-plan.conf"])


if __name__ == "__main__":
    unittest.main()
