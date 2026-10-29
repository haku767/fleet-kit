import contextlib
import io
import os
import pathlib
import shutil
import tempfile
import tomllib
import unittest

from fleet_kit import cli, edit, model

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"

WITH_GUARDS = '''[fleet]
name = "t"
owner = "o"

[machines.box]
role = "bots"
family = "arch"
home_root = "/srv"

[stacks.py]
test = "pytest"
allow = ["pytest*"]

[projects.a]
repo = "o/a"
stacks = ["py"]
dev = { name = "a-dev" }   # the one bot

# the guards
[guards.g]
machine = "box"
apps = ["o-g"]
'''


class EditTests(unittest.TestCase):
    def test_toml_values(self):
        self.assertEqual(edit.toml_value({"name": "x", "n": 1, "b": True, "l": ["a", "b"]}),
                         '{ name = "x", n = 1, b = true, l = ["a", "b"] }')
        self.assertEqual(edit.toml_value('say "hi"'), '"say \\"hi\\""')

    def test_insert_project_before_guards_keeps_their_comment(self):
        block = edit.project_block("b", {"repo": "o/b", "stacks": ["py"], "x": None, "plan": {"name": "b-plan"}})
        out = edit.insert_project(WITH_GUARDS, block)
        self.assertLess(out.index("[projects.b]"), out.index("# the guards\n[guards.g]"))
        self.assertIn('dev = { name = "a-dev" }   # the one bot\n\n[projects.b]\nrepo = "o/b"\nstacks = ["py"]\nplan = { name = "b-plan" }\n\n# the guards', out)
        doc = tomllib.loads(out)
        self.assertEqual(list(doc["projects"]), ["a", "b"])
        self.assertEqual([str(x) for x in model.check(out, doc) if x.level == "error"], [])

    def test_insert_project_at_the_end_without_guards(self):
        text = MINIMAL.read_text()
        out = edit.insert_project(text, edit.project_block("z", {"repo": "ErikNeu777/z", "stacks": ["python-uv"]}))
        self.assertTrue(out.endswith('[projects.z]\nrepo = "ErikNeu777/z"\nstacks = ["python-uv"]\n'))
        self.assertEqual(list(tomllib.loads(out)["projects"]), ["myapp", "z"])

    def test_insert_bot_lands_in_its_project(self):
        out = edit.insert_bot(WITH_GUARDS, "a", "plan", {"name": "a-plan"})
        self.assertIn('dev = { name = "a-dev" }   # the one bot\nplan = { name = "a-plan" }\n\n# the guards', out)
        doc = tomllib.loads(out)
        self.assertEqual(doc["projects"]["a"]["plan"], {"name": "a-plan"})
        self.assertEqual(doc["guards"]["g"]["apps"], ["o-g"])
        with self.assertRaises(KeyError):
            edit.insert_bot(WITH_GUARDS, "nope", "plan", {"name": "x"})

    def test_steps_mention_apps_labels_and_a_foreign_owner(self):
        text = MINIMAL.read_text().replace("ErikNeu777/myapp", "Other/myapp")
        doc = tomllib.loads(text)
        lines = edit.github_steps(doc, "myapp", ["plan", "dev"], "box")
        joined = "\n".join(lines)
        self.assertIn("`ErikNeu777-myapp-plan`, `ErikNeu777-myapp-dev`", joined)
        self.assertIn("Other's account (Other installs it)", joined)
        self.assertIn("fleet labels myapp --apply", joined)
        self.assertNotIn("fleet ruleset", joined)
        self.assertIn("/srv/<account>/.config/fleet/app-id", joined)


class CliEditTests(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_add_project_then_add_bot(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "fleet.toml")
            shutil.copy(MINIMAL, f)
            os.chmod(f, 0o664)
            rc, out, err = self.run_cli(["add-project", "tool", "--repo", "ErikNeu777/tool", "--stack", "python-uv",
                                         "--reviews", "1", "--no-plan", f])
            self.assertEqual(rc, 0, err)
            self.assertIn("appended [projects.tool] (ErikNeu777/tool, bots tool-dev)", out)
            self.assertIn("fleet ruleset tool --apply", out)
            self.assertEqual(oct(os.stat(f).st_mode & 0o777), "0o664")
            rc, out, err = self.run_cli(["add-project", "tool", "--repo", "ErikNeu777/tool", "--stack", "python-uv", f])
            self.assertEqual(rc, 1)
            self.assertIn("in the file already", err)
            rc, out, err = self.run_cli(["add-bot", "tool", "plan", "--persona", "Kiri", f])
            self.assertEqual(rc, 0, err)
            self.assertIn('added plan = { name = "tool-plan", persona = "Kiri" }', out)
            rc, out, err = self.run_cli(["add-bot", "tool", "plan", f])
            self.assertEqual(rc, 1)
            text, doc = model.load(f)
            self.assertEqual([str(x) for x in model.check(text, doc)], [])
            self.assertEqual(sorted(b.name for b in model.bots(doc)), ["myapp-dev", "myapp-plan", "tool-dev", "tool-plan"])

    def test_nothing_written_when_the_result_would_be_wrong(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "fleet.toml")
            shutil.copy(MINIMAL, f)
            before = open(f).read()
            rc, out, err = self.run_cli(["add-project", "tool", "--repo", "ErikNeu777/tool", "--stack", "nope", f])
            self.assertEqual(rc, 1)
            self.assertIn("stack `nope`", err)
            self.assertIn("nothing written", err)
            self.assertEqual(open(f).read(), before)
            rc, out, err = self.run_cli(["add-project", "tool", "--repo", "ErikNeu777/tool", "--stack", "python-uv",
                                         "--dev", "myapp-dev", f])
            self.assertEqual(rc, 1)
            self.assertIn("already used", err)
            self.assertEqual(open(f).read(), before)


if __name__ == "__main__":
    unittest.main()
