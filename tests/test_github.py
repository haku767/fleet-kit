import pathlib
import tomllib
import unittest

from fleet_kit import labels, model, ruleset

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"


class FakeGH:
    """Answers GETs from a dict path -> value and records every write."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get(self, path, paginate=False):
        return self.answers[path]

    def post(self, path, body):
        self.calls.append(("POST", path, body))

    def patch(self, path, body):
        self.calls.append(("PATCH", path, body))

    def put(self, path, body):
        self.calls.append(("PUT", path, body))


def minimal(extra=""):
    text = MINIMAL.read_text() + extra
    doc = tomllib.loads(text)
    return text, doc


class LabelTests(unittest.TestCase):
    def test_defaults_fill_the_placeholders(self):
        _, doc = minimal()
        want = {l["name"]: l for l in labels.desired(doc, "myapp")}
        self.assertEqual(want["ready"]["description"], "Acceptance criteria set; myapp-dev may build it")
        self.assertEqual(want["next"]["description"], "The ready issue to build first; set by myapp-plan")
        self.assertIn("ErikNeu777", want["needs-haku"]["description"])
        self.assertEqual(want["bot:myapp-dev"]["description"], "written by or for the bot myapp-dev")
        self.assertEqual(len(want), len(labels.DEFAULT_LABELS) - 1 + 2)

    def test_own_labels_table_replaces_the_defaults(self):
        text, doc = minimal('\n[labels]\nready = { color = "0E8A16", description = "go, {dev}" }\n')
        self.assertEqual([str(x) for x in model.check(text, doc)], [])
        self.assertEqual(labels.desired(doc, "myapp"), [{"name": "ready", "color": "0e8a16", "description": "go, myapp-dev"}])

    def test_labels_table_checks(self):
        text, doc = minimal('\n[labels]\nready = { color = "green" }\n"bot:{name}" = { color = "ededed" }\n"b:{name}" = { color = "ededed" }\n')
        e = [str(x) for x in model.check(text, doc) if x.level == "error"]
        self.assertTrue(any("six hex digits" in x for x in e))
        self.assertTrue(any("only one label may hold {name}" in x for x in e))
        text, _ = minimal()
        text = text.replace('name = "erik"', 'name = "erik"\nlabels = ["ready"]')   # the old place
        w = [str(x) for x in model.check(text, tomllib.loads(text))]
        self.assertEqual(len(w), 1)
        self.assertIn("moved to the [labels] table", w[0])

    def test_plan_and_apply(self):
        _, doc = minimal()
        want = labels.desired(doc, "myapp")
        have = [{"name": "Ready", "color": "0e8a16", "description": "old words"},
                {"name": "size:S", "color": "C2E0C6", "description": "Small: one file or one function; Sonnet"},
                {"name": "bug", "color": "d73a4a", "description": "Something isn't working"}]
        pl = labels.plan(want, have)
        self.assertEqual(pl["ok"], ["size:S"])
        self.assertEqual([l["name"] for l, _ in pl["update"]], ["ready"])
        self.assertEqual(pl["other"], ["bug"])
        self.assertEqual(len(pl["create"]), len(want) - 2)
        lines = labels.describe(pl)
        self.assertTrue(any("name 'Ready' -> 'ready'" in x and "description 'old words' ->" in x for x in lines))
        gh = FakeGH({})
        n = labels.apply(gh, "ErikNeu777/myapp", pl)
        self.assertEqual(n, len(want) - 1)
        self.assertEqual(gh.calls[-1][:2], ("PATCH", "repos/ErikNeu777/myapp/labels/Ready"))
        self.assertEqual(gh.calls[-1][2]["new_name"], "ready")
        posts = [c for c in gh.calls if c[0] == "POST"]
        self.assertTrue(all(c[1] == "repos/ErikNeu777/myapp/labels" for c in posts))
        self.assertIn("repos/ErikNeu777/myapp/labels", [c[1] for c in posts])
        colon = [c for c in gh.calls if "%3A" in c[1]]
        self.assertEqual(colon, [])   # creating sends the name in the body; only PATCH quotes the path


class RulesetTests(unittest.TestCase):
    def have(self, reviews=1, extra_rule=None, **over):
        r = ruleset.wanted(reviews)
        r.update({"id": 7, "bypass_actors": [], "rules": [dict(x) for x in r["rules"]]})
        r["rules"][2] = {"type": "pull_request", "parameters": dict(r["rules"][2]["parameters"],
                         require_extra_approval_for_unattributed_changes=True)}   # a field GitHub adds
        if extra_rule:
            r["rules"].append({"type": extra_rule})
        r.update(over)
        return r

    def test_equal_up_to_github_extras(self):
        self.assertEqual(ruleset.diff(ruleset.wanted(1), self.have()), [])
        self.assertEqual(ruleset.diff(ruleset.wanted(1), None), ["missing"])

    def test_differences(self):
        d = ruleset.diff(ruleset.wanted(1), self.have(reviews=0, extra_rule="required_signatures", enforcement="disabled",
                                                       bypass_actors=[{"actor_id": 1}]))
        self.assertIn("enforcement disabled -> active", d)
        self.assertIn("pull_request.required_approving_review_count 0 -> 1", d)
        self.assertTrue(any("bypass actor" in x for x in d))
        self.assertIn("rule required_signatures that the fleet does not set (kept)", d)

    def test_apply_creates_or_updates_keeping_extra_rules(self):
        gh = FakeGH({})
        self.assertEqual(ruleset.apply(gh, "o/r", ruleset.wanted(1), None), "created")
        self.assertEqual(gh.calls[0][:2], ("POST", "repos/o/r/rulesets"))
        self.assertEqual(gh.calls[0][2]["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])
        have = self.have(reviews=0, extra_rule="required_signatures")
        self.assertEqual(ruleset.apply(gh, "o/r", ruleset.wanted(1), have), "updated")
        self.assertEqual(gh.calls[1][:2], ("PUT", "repos/o/r/rulesets/7"))
        self.assertEqual([r["type"] for r in gh.calls[1][2]["rules"]],
                         ["deletion", "non_fast_forward", "pull_request", "required_signatures"])
        self.assertEqual(gh.calls[1][2]["rules"][2]["parameters"]["required_approving_review_count"], 1)

    def test_fetch_reads_each_ruleset(self):
        gh = FakeGH({"repos/o/r/rulesets": [{"id": 1, "name": "main-rule"}, {"id": 2, "name": "x"}],
                     "repos/o/r/rulesets/1": {"id": 1, "name": "main-rule", "rules": []},
                     "repos/o/r/rulesets/2": {"id": 2, "name": "x", "rules": []}})
        self.assertEqual([r["name"] for r in ruleset.fetch(gh, "o/r")], ["main-rule", "x"])

    def test_reviews_key_checked(self):
        text, doc = minimal()
        text = text.replace('ci = "auto"', 'ci = "auto"\nreviews = -1')
        e = [str(x) for x in model.check(text, tomllib.loads(text)) if x.level == "error"]
        self.assertTrue(any("reviews must be a whole number" in x for x in e))
        text = text.replace("reviews = -1", "reviews = 0")
        self.assertEqual([str(x) for x in model.check(text, tomllib.loads(text))], [])


if __name__ == "__main__":
    unittest.main()
