"""`fleet ruleset <project>`: the rule on the default branch.

A project with `reviews = N` in fleet.toml wants the ruleset RULESET_NAME on its default
branch: no deletion, no force push, changes through a pull request with N approving reviews
(0 still requires the pull request). A project without `reviews` wants none; the command then
only reports what the repository has. Creating or changing a ruleset needs admin rights on the
repository: the owner runs it, not a bot."""

RULESET_NAME = "main-rule"


def wanted(reviews):
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": int(reviews),
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": False,
                "allowed_merge_methods": ["merge", "squash", "rebase"],
            }},
        ],
    }


def diff(want, have):
    """What differs between the wanted ruleset and the repository's (None: missing)."""
    if have is None:
        return ["missing"]
    d = []
    if have.get("enforcement") != want["enforcement"]:
        d.append(f"enforcement {have.get('enforcement')} -> {want['enforcement']}")
    if have.get("target") != want["target"]:
        d.append(f"target {have.get('target')} -> {want['target']}")
    inc = (have.get("conditions") or {}).get("ref_name", {}).get("include")
    if inc != want["conditions"]["ref_name"]["include"]:
        d.append(f"branches {inc} -> {want['conditions']['ref_name']['include']}")
    if have.get("bypass_actors"):
        d.append(f"{len(have['bypass_actors'])} bypass actor(s) that the fleet does not want")
    rules = {r["type"]: r for r in have.get("rules") or []}
    for r in want["rules"]:
        h = rules.get(r["type"])
        if h is None:
            d.append(f"rule {r['type']} missing")
            continue
        for k, v in (r.get("parameters") or {}).items():
            hv = (h.get("parameters") or {}).get(k)
            differs = sorted(hv or []) != sorted(v) if isinstance(v, list) else hv != v
            if differs:
                d.append(f"{r['type']}.{k} {hv!r} -> {v!r}")
    for t in rules:
        if t not in {r["type"] for r in want["rules"]}:
            d.append(f"rule {t} that the fleet does not set (kept)")
    return d


def merged(want, have):
    """The body to send: the wanted ruleset, keeping rules of other types the repository has."""
    body = dict(want)
    extra = [r for r in (have or {}).get("rules") or [] if r["type"] not in {r["type"] for r in want["rules"]}]
    body["rules"] = want["rules"] + extra
    return body


def fetch(gh, repo):
    """All rulesets of the repository with their rules: list of dicts."""
    out = []
    for r in gh.get(f"repos/{repo}/rulesets") or []:
        out.append(gh.get(f"repos/{repo}/rulesets/{r['id']}"))
    return out


def apply(gh, repo, want, have):
    if have is None:
        gh.post(f"repos/{repo}/rulesets", merged(want, None))
        return "created"
    gh.put(f"repos/{repo}/rulesets/{have['id']}", merged(want, have))
    return "updated"
