import pathlib
import tomllib
import unittest

from fleet_kit import doctor, labels, model, ruleset
from fleet_kit.github import GHError

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"


class UnusableGHTests(unittest.TestCase):
    def test_no_token_is_one_skip(self):
        _, doc = model.load(MINIMAL)
        gh = FakeGH({"user": GHError("gh api GET user: To get started with GitHub CLI, please run:  gh auth login")})
        f = doctor.run(gh, doc, ["myapp"])
        self.assertEqual([x.level for x in f], ["skip"])
        self.assertIn("gh has no token", f[0].message)


class FakeGH:
    """GET answers from a dict; a value that is an Exception is raised."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def get(self, path, paginate=False):
        self.asked.append(path)
        key = path.split("?")[0]
        if key not in self.answers:
            raise GHError(f"gh api GET {path}: Not Found (no such repository, or your login or App cannot see it)")
        v = self.answers[key]
        if isinstance(v, Exception):
            raise v
        return v


def good_world(doc, reviews=None, as_owner=True):
    """Answers for a repository that is exactly as the file wants it."""
    repo = "example/myapp"
    want = labels.desired(doc, "myapp")
    a = {
        f"repos/{repo}": {"default_branch": "main", "archived": False},
        f"repos/{repo}/labels": [dict(l) for l in want],
        f"repos/{repo}/rulesets": [],
        f"repos/{repo}/issues": [{"number": 3, "title": "x"}],
        f"repos/{repo}/issues/3/timeline": [{"event": "labeled", "label": {"name": "ready"}, "actor": {"login": "example"}}],
        "apps/example-myapp-plan": {"permissions": {"contents": "write", "issues": "write", "pull_requests": "write",
                                                       "metadata": "read", "checks": "read", "actions": "read"}},
        "apps/example-myapp-dev": {"permissions": {"contents": "write", "issues": "write", "pull_requests": "write",
                                                      "metadata": "read", "checks": "read", "actions": "read"}},
    }
    if as_owner:
        a["user"] = {"login": "example"}
        a["user/installations"] = [{"id": 1, "app_slug": "example-myapp-plan"}, {"id": 2, "app_slug": "example-myapp-dev"}]
        a["user/installations/1/repositories"] = [{"full_name": repo}]
        a["user/installations/2/repositories"] = [{"full_name": repo}]
    if reviews is not None:
        r = ruleset.wanted(reviews)
        r["id"] = 9
        a[f"repos/{repo}/rulesets"] = [{"id": 9, "name": "main-rule"}]
        a[f"repos/{repo}/rulesets/9"] = r
    return a


def levels(findings):
    return [(f.level, f.where, f.message) for f in findings]


class DoctorTests(unittest.TestCase):
    def test_all_ok_as_the_owner(self):
        _, doc = (lambda t: (t, tomllib.loads(t)))(MINIMAL.read_text())
        gh = FakeGH(good_world(doc))
        out = doctor.run(gh, doc, ["myapp"])
        self.assertEqual([f.level for f in out], ["ok"] * len(out), "\n".join(map(str, out)))
        self.assertTrue(any("installed on example/myapp" in f.message for f in out))
        self.assertTrue(any("1 open ready issue(s), all set by someone trusted" in f.message for f in out))

    def test_bot_token_skips_what_it_cannot_see(self):
        _, doc = (lambda t: (t, tomllib.loads(t)))(MINIMAL.read_text())
        a = good_world(doc, as_owner=False)
        del a["apps/example-myapp-dev"]
        gh = FakeGH(a)
        out = doctor.run(gh, doc, ["myapp"])
        skips = [f.message for f in out if f.level == "skip"]
        self.assertEqual(len(skips), 3, skips)   # two installations, one invisible App
        self.assertTrue(any("not visible to you" in m for m in skips))
        self.assertEqual([f for f in out if f.level == "fail"], [])

    def test_failures_and_warnings(self):
        text = MINIMAL.read_text().replace('ci = "auto"', 'ci = "auto"\nreviews = 1\nrunner = "box-myapp"')
        doc = tomllib.loads(text)
        a = good_world(doc)   # no ruleset although reviews = 1
        a["repos/example/myapp/labels"] = a["repos/example/myapp/labels"][1:]   # one label missing
        a["repos/example/myapp/issues/3/timeline"] = [{"event": "labeled", "label": {"name": "ready"}, "actor": {"login": "someone"}}]
        a["apps/example-myapp-dev"]["permissions"].pop("checks")
        del a["apps/example-myapp-plan"]
        a["user/installations/2/repositories"] = []
        a["repos/example/myapp/actions/runners"] = [{"name": "box-myapp", "status": "offline"}]
        out = doctor.run(FakeGH(a), doc, ["myapp"])
        fails = [f.message for f in out if f.level == "fail"]
        warns = [f.message for f in out if f.level == "warn"]
        self.assertTrue(any("ruleset main-rule missing" in m for m in fails))
        self.assertTrue(any("set by someone" in m and "must not build" in m for m in fails))
        self.assertTrue(any("example-myapp-plan does not exist" in m for m in fails))
        self.assertTrue(any("installed but not on example/myapp" in m for m in fails))
        self.assertTrue(any("1 to create" in m for m in warns))
        self.assertTrue(any("lacks checks read" in m for m in warns))
        self.assertTrue(any("runner box-myapp is offline" in m for m in warns))

    def test_no_ci_wants_no_checks_permission(self):
        text = MINIMAL.read_text().replace('ci = "auto"', 'ci = "no"')
        doc = tomllib.loads(text)
        a = good_world(doc)
        a["apps/example-myapp-dev"]["permissions"] = {"contents": "write", "issues": "write", "pull_requests": "write", "metadata": "read"}
        out = doctor.run(FakeGH(a), doc, ["myapp"])
        self.assertEqual([f.level for f in out], ["ok"] * len(out), "\n".join(map(str, out)))

    def test_unreachable_repository_stops_there(self):
        doc = tomllib.loads(MINIMAL.read_text())
        out = doctor.run(FakeGH({}), doc, ["myapp"])
        self.assertEqual([f.level for f in out[1:]], ["fail"])
        self.assertIn("Not Found", out[1].message)

    def test_foreign_owner_repository_is_a_skip(self):
        """A repository under another owner that the token cannot see: skip, naming the token to use."""
        doc = tomllib.loads(MINIMAL.read_text())
        doc["projects"]["myapp"]["repo"] = "Someone/their-app"
        doc["projects"]["myapp"]["owner_app"] = "their"
        out = doctor.run(FakeGH({}), doc, ["myapp"])
        self.assertEqual([f.level for f in out[1:]], ["skip"])
        self.assertIn("fleet-token --app their", out[1].message)
        del doc["projects"]["myapp"]["owner_app"]
        out = doctor.run(FakeGH({}), doc, ["myapp"])
        self.assertEqual(out[1].level, "skip")
        self.assertIn("Someone's repositories", out[1].message)


if __name__ == "__main__":
    unittest.main()
