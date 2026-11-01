import io
import json
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fleet_kit import cli, status

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"


def iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def sample(generated_min_ago=3):
    return {
        "generated": iso(generated_min_ago),
        "page": {"set": "default", "sets": ["default"], "labels": {"ready": "ready", "next": "next", "decide": "needs-owner", "live": "live"}, "owner": "example", "fleet": "test"},
        "projects": [
            {"repo": "example/app", "name": "app", "prs": [{"number": 1, "draft": False}, {"number": 3, "draft": True}],
             "issues": [{"number": 10, "labels": ["ready"]}, {"number": 11, "labels": ["ready", "live"]}, {"number": 12, "labels": ["next", "ready"]}]},
            {"repo": "other/lib", "name": "lib", "prs": [], "issues": [], "error": "no token: x"},
        ],
        "bots": [
            {"name": "app-dev", "kind": "dev", "project": "app", "machine": "box", "rounds": "round", "last": {"outcome": "running", "what": "#10", "ended": iso(1)},
             "cost_today": 1.25, "rounds_today": 3, "timer": {"active": True}},
            {"name": "app-plan", "kind": "plan", "project": "app", "machine": "box", "rounds": "review", "last": {"outcome": "reviewed", "ended": iso(5), "signed_in": True},
             "cost_today": 0.1, "rounds_today": 1, "timer": {"active": False}},
            {"name": "lap-guard", "kind": "guard", "project": "lib", "machine": "laptop", "rounds": "none"},
        ],
        "guards": [{"host": "box", "head": "ok", "age_min": 4, "findings": ["WARN a", "CRIT b"]}, {"host": "laptop", "error": "no file"}],
        "machine": {"name": "box", "disks": {"/": {"free_gb": 40, "size_gb": 100}}, "load": [0.5, 0.4, 0.3], "uptime_h": 12.5, "gpu": {"util_pct": 7, "lock": "someone"}},
        "items": [
            {"id": "merge:example/app#1", "kind": "merge", "tag": "merge", "project": "app", "text": "a title", "label": "#1", "why": "approved by app-plan, CI green", "since": iso(3000)},
            {"id": "decide:example/app#12", "kind": "decide", "tag": "decide", "project": "app", "text": "decide me", "label": "#12", "since": iso(10)},
            {"id": "step:error:other/lib", "kind": "step", "tag": "error", "project": "lib", "text": "no token: x", "since": iso(10)},
            {"id": "step:timer:app-plan", "kind": "step", "tag": "timer off", "project": "app", "text": "", "bot": "app-plan", "why": "1 pull request waits for its review", "since": iso(10)},
        ],
        "history": {"days": [], "projects": {}, "bots": {}},
        "sets": {},
    }


class StatusTests(unittest.TestCase):
    def test_lines(self):
        out = status.lines(sample())
        text = "\n".join(out)
        self.assertTrue(out[0].startswith("test fleet · collected 3 min ago"))
        self.assertNotIn("STALE", out[0])
        self.assertIn("Merge (1)", out)
        self.assertIn("Decide (1)", out)
        self.assertIn("Step in (2)", out)
        self.assertRegex(text, r"app\s+merge\s+#1 a title  \(approved by app-plan, CI green\)  · 2 d")
        self.assertRegex(text, r"app\s+timer off\s+app-plan  \(1 pull request waits for its review\)")
        self.assertRegex(text, r"  app\s+2 ready · 1 open pull request · next #12")   # the live one is not queued; the draft not open
        self.assertRegex(text, r"  lib\s+error: no token: x")
        self.assertRegex(text, r"app-dev\s+dev\s+app\s+running #10  today 3 runs \$1.25  timer on")
        self.assertRegex(text, r"app-plan\s+plan\s+app\s+paused  today 1 runs \$0.10  timer off")
        self.assertRegex(text, r"lap-guard\s+guard\s+lib\s+on laptop")
        self.assertIn("Machine box: up 12.5 h · load 0.5 0.4 0.3 · / 40 GB free · GPU 7 % · GPU lock: someone", out)
        self.assertIn("Guards: box 2 open, 1 CRIT (4 min) · laptop no report", out)

    def test_project_filter_and_empty(self):
        out = status.lines(sample(), "lib")
        self.assertIn("Step in (1)", out)
        self.assertNotIn("Merge (1)", out)
        self.assertFalse(any(l.startswith("Machine") for l in out))
        self.assertEqual([l for l in out if l.startswith("  lap-guard")][0].split()[2], "lib")
        d = sample()
        d["items"] = []
        self.assertIn("nothing waits on you", status.lines(d))

    def test_states(self):
        d = sample()
        b = d["bots"][1]
        b["timer"]["active"] = True
        self.assertEqual(status.state_word(b, "box"), "reviewed 5 min ago")
        b["last"]["ended"] = iso(90)
        self.assertEqual(status.state_word(b, "box"), "idle 2 h")
        b["last"]["signed_in"] = False
        self.assertEqual(status.bot_state(b, "box"), "bad")
        b["last"] = None
        self.assertEqual(status.state_word(b, "box"), "no round yet")

    def test_main_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp, "fleet.json")
            f.write_text(json.dumps(sample()))
            buf = io.StringIO()
            self.assertEqual(status.main(f, None, out=buf), 0)
            self.assertIn("Merge (1)", buf.getvalue())
            f.write_text(json.dumps(sample(45)))
            buf = io.StringIO()
            self.assertEqual(status.main(f, None, out=buf), 2)
            self.assertIn("STALE", buf.getvalue())
            self.assertEqual(status.main(f, "nope", out=io.StringIO()), 1)
            self.assertEqual(status.main(pathlib.Path(tmp, "missing.json"), None, out=io.StringIO()), 1)
            f.write_text("{not json")
            self.assertEqual(status.main(f, None, out=io.StringIO()), 1)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp, "fleet.json")
            f.write_text(json.dumps(sample()))
            self.assertEqual(cli.main(["status", "--json", str(f)]), 0)
            self.assertEqual(cli.main(["status", "app", "--json", str(f)]), 0)
            # without --json the path comes from the file: agents_dir/fleet/fleet.json, which is not there
            self.assertEqual(cli.main(["status", str(MINIMAL)]), 1)
            self.assertEqual(cli.main(["status", "myapp", str(MINIMAL)]), 1)

    def test_default_file(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                # the installed file's name points into the temporary directory first: on a machine
                # that has /etc/fleet/fleet.toml the first assertion would otherwise see it
                saved, cli.INSTALLED_FILE = cli.INSTALLED_FILE, str(pathlib.Path(tmp, "etc.toml"))
                try:
                    self.assertEqual(cli.default_file(), "fleet.toml")   # nothing anywhere: the error names fleet.toml
                    pathlib.Path(tmp, "etc.toml").write_text(MINIMAL.read_text())
                    self.assertEqual(cli.default_file(), cli.INSTALLED_FILE)
                    self.assertEqual(cli.main(["check"]), 0)   # the installed file
                    pathlib.Path(tmp, "fleet.toml").write_text("[fleet]\nname = 1\n")
                    self.assertEqual(cli.default_file(), "fleet.toml")   # the working directory's wins
                    self.assertEqual(cli.main(["check"]), 1)
                finally:
                    cli.INSTALLED_FILE = saved
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
