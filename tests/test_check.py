import pathlib
import tomllib
import unittest

from fleet_kit import model

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


if __name__ == "__main__":
    unittest.main()
