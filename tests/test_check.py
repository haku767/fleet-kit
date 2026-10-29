import pathlib
import tomllib
import unittest

from fleet_kit import model, render

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"


def check_text(text):
    return model.check(text, tomllib.loads(text))


def errors(findings):
    return [str(x) for x in findings if x.level == "error"]


class CheckTests(unittest.TestCase):
    def test_minimal_example_is_clean(self):
        text, doc = model.load(MINIMAL)
        self.assertEqual([str(x) for x in model.check(text, doc)], [])
        self.assertEqual(len(model.bots(doc)), 2)

    def test_missing_owner_and_repo_shape(self):
        text = MINIMAL.read_text().replace('owner = "ErikNeu777"', "").replace("ErikNeu777/myapp", "myapp")
        e = errors(check_text(text))
        self.assertTrue(any("fleet.owner" in x for x in e))
        self.assertTrue(any("repo must be owner/name" in x for x in e))

    def test_duplicate_bot_name(self):
        text = MINIMAL.read_text().replace('dev  = { name = "myapp-dev" }', 'dev  = { name = "myapp-plan" }')
        e = errors(check_text(text))
        self.assertTrue(any("already used" in x for x in e))

    def test_unknown_stack_and_ci(self):
        text = MINIMAL.read_text().replace('stacks = ["python-uv"]', 'stacks = ["rust"]').replace('ci = "auto"', 'ci = "maybe"')
        e = errors(check_text(text))
        self.assertTrue(any("stack `rust`" in x for x in e))
        self.assertTrue(any("ci must be" in x for x in e))

    def test_secret_in_file(self):
        text = MINIMAL.read_text() + '\n[people.x]\nrole = "member"\n# token = "ghp_' + "a" * 36 + '"\n'
        e = errors(check_text(text))
        self.assertTrue(any("looks like a secret" in x for x in e))

    def test_two_bot_machines_need_a_choice(self):
        text = MINIMAL.read_text() + '\n[machines.box2]\nrole = "bots"\nfamily = "nixos"\nhome_root = "/srv"\n'
        e = errors(check_text(text))
        self.assertTrue(any("say which one" in x for x in e))

    def test_unknown_key_warns(self):
        text = MINIMAL.read_text().replace('[projects.myapp]', '[projects.myapp]\nrepo_url = "x"')
        w = [str(x) for x in check_text(text) if x.level == "warn"]
        self.assertTrue(any("unknown key `repo_url`" in x for x in w))


TWO_STACKS = """
[fleet]
name = "t"
owner = "own"
[machines.m]
role = "bots"
family = "arch"
home_root = "/srv"
scratch_root = "/data"
[stacks.rust]
test = "cargo fmt --check && cargo test --locked"
allow = ["cargo fmt *", "cargo test *"]
env = { CARGO_TARGET_DIR = "{scratch}/target" }
[stacks.nix]
test = "nix develop -c {each}"
note = "nix flake check when flake.nix changed"
allow = ["nix *"]
[projects.p]
repo = "own/p"
stacks = ["rust", "nix"]
test_extra = "extra-check"
allow_extra = ["extra-check"]
ci = "auto"
ready_also = ["friend"]
review_note = "be careful"
plan = { name = "p-plan", account = "pp" }
dev  = { name = "p-dev" }
"""


class RenderTests(unittest.TestCase):
    def test_minimal_renders_two_confs_and_lists(self):
        text, doc = model.load(MINIMAL)
        files = render.render(doc)
        self.assertIn("bots/myapp-dev.conf", files)
        self.assertIn("bots/myapp-plan.conf", files)
        self.assertIn("machines/box/lists.sh", files)
        dev = files["bots/myapp-dev.conf"]
        self.assertIn("kind='dev'", dev)
        self.assertIn("trusted='ErikNeu777 ErikNeu777-myapp-plan[bot]'", dev)
        self.assertIn("Bash(uv sync*)", dev)
        self.assertIn("export UV_CACHE_DIR='/data/myapp-dev/uv-cache'", dev)
        self.assertIn("While the repository has no CI", dev)
        plan = files["bots/myapp-plan.conf"]
        self.assertIn("app='ErikNeu777-myapp-dev'", plan)
        self.assertIn("reviewer='ErikNeu777-myapp-plan[bot]'", plan)
        self.assertIn("round_bots='myapp-dev'", files["machines/box/lists.sh"])
        self.assertIn("review_bots='myapp-plan'", files["machines/box/lists.sh"])

    def test_addon_stack_account_and_ready_also(self):
        files = render.render(tomllib.loads(TWO_STACKS))
        dev = files["bots/p-dev.conf"]
        self.assertIn("test_cmd='cargo fmt --check && cargo test --locked; extra-check. While the repository", dev)
        self.assertIn("test_cmd_nix='inside the repository'\\''s dev shell, one per call: nix develop -c cargo fmt --check; "
                      "nix develop -c cargo test --locked; extra-check; nix flake check when flake.nix changed'", dev)
        self.assertIn("trusted='own own-p-plan[bot] friend'", dev)
        self.assertIn("Bash(nix *)", dev)
        self.assertIn("Bash(extra-check)", dev)
        plan = files["bots/pp.conf"]   # named after the account
        self.assertIn("account='pp'", plan)
        self.assertIn("name='p-plan'", plan)
        self.assertIn("review_note='be careful'", plan)
        self.assertIn("bots='p-dev pp'", files["machines/m/lists.sh"].replace("'pp p-dev'", "'p-dev pp'"))

    def test_addon_takes_its_own_test_extra(self):
        text = TWO_STACKS.replace('test_extra = "extra-check"', 'test_extra = "extra-check"\ntest_extra_nix = "nix develop -c extra-check"')
        self.assertEqual([str(x) for x in check_text(text) if "unknown key" in str(x)], [])
        dev = render.render(tomllib.loads(text))["bots/p-dev.conf"]
        self.assertIn("test_cmd='cargo fmt --check && cargo test --locked; extra-check.", dev)
        self.assertIn("nix develop -c cargo test --locked; nix develop -c extra-check; nix flake check", dev)
        self.assertTrue(any("unknown key `test_extra_zig`" in str(x)
                            for x in check_text(TWO_STACKS + '\n[projects.q]\nrepo = "own/q"\ntest_extra_zig = "x"\n')))

    def test_guard_project(self):
        text = TWO_STACKS + '\n[projects.notes]\nrepo = "own/notes"\n[guards.g]\nmachine = "m"\nproject = "notes"\napps = ["own-g"]\n'
        self.assertEqual(errors(check_text(text)), [])
        import json
        tables = json.loads(render.render(tomllib.loads(text))["tables.json"])
        self.assertIn(["g", "guard", "notes", "m", "none", "g"], tables["bots"])
        bad = text.replace('project = "notes"', 'project = "nope"')
        self.assertTrue(any("project `nope`" in x for x in errors(check_text(bad))))

    def test_conf_is_valid_sh(self):
        import subprocess
        files = render.render(tomllib.loads(TWO_STACKS))
        for name in ("bots/p-dev.conf", "bots/pp.conf", "machines/m/lists.sh"):
            r = subprocess.run(["sh", "-c", files[name] + "\necho \"$name|$repo|${trusted:-}|${round_bots:-}\""],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run(["sh", "-c", files["bots/p-dev.conf"] + "\nprintf %s \"$test_cmd_nix\""], capture_output=True, text=True)
        self.assertIn("repository's dev shell", r.stdout)

    def test_apply_conf_adds_changes_removes(self):
        import os
        import tempfile
        files = render.render(tomllib.loads(TWO_STACKS))
        with tempfile.TemporaryDirectory() as d:
            acts = render.apply_conf(files, d)
            self.assertEqual(sorted(w for w, _ in acts), ["added", "added"])
            self.assertEqual(oct(os.stat(os.path.join(d, "p-dev.conf")).st_mode & 0o777), "0o644")
            self.assertEqual([w for w, _ in render.apply_conf(files, d)], ["kept", "kept"])
            with open(os.path.join(d, "mine.conf"), "w") as f:
                f.write("# hand-written\nx=1\n")
            files2 = {k: v for k, v in files.items() if k != "bots/pp.conf"}
            files2["bots/p-dev.conf"] = files2["bots/p-dev.conf"] + "extra='1'\n"
            acts = render.apply_conf(files2, d, dry_run=True)
            self.assertEqual(sorted(w for w, _ in acts), ["changed", "removed"])
            self.assertTrue(os.path.exists(os.path.join(d, "pp.conf")))   # dry run wrote nothing
            acts = render.apply_conf(files2, d)
            self.assertFalse(os.path.exists(os.path.join(d, "pp.conf")))
            self.assertTrue(os.path.exists(os.path.join(d, "mine.conf")))   # never a hand-written file
            self.assertIn("extra='1'", open(os.path.join(d, "p-dev.conf")).read())


if __name__ == "__main__":
    unittest.main()
