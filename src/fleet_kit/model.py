"""The fleet's file: load it, check it.

A finding is (level, where, message); level is "error" or "warn". `check()` never raises on
a bad file, it returns findings; only an unreadable or unparsable file raises.
"""

import re
import tomllib
from dataclasses import dataclass

FAMILIES = ("arch", "nixos")
ROLES = ("bots", "guard")
CI = ("yes", "no", "auto")
PEOPLE_ROLES = ("owner", "member", "tester")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]*$")
COLOR = re.compile(r"^[0-9a-fA-F]{6}$")

# What a secret looks like when it ends up in the file by mistake: GitHub tokens of every kind,
# an Anthropic key, a PEM block, a long hex or base64 run. Facts only, never a key.
SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[0-9a-f]{40,}\b|\b[A-Za-z0-9+/]{60,}={0,2}\b"
)

KEYS = {
    "fleet": {"name", "owner", "agents_dir", "trailers", "rules"},
    "defaults.round": {"every", "max_worktrees", "max_turns", "budget_usd", "model_small", "model_large"},
    "defaults.review": {"every", "max_per_pr", "max_turns", "budget_usd", "model_small", "model_large"},
    "defaults.page": {"budget_dev_usd", "budget_plan_usd", "collect_every"},
    "machine": {"role", "family", "home_root", "scratch_root", "page", "guard", "courier"},
    "stack": {"test", "allow", "env", "note"},
    "project": {"repo", "stacks", "test", "test_extra", "test_note", "ci", "ci_note", "ci_checks", "review_note",
                "allow_extra", "env", "runner", "reads", "deploy", "paused", "owner_app", "ready_also", "plan", "dev", "machine",
                "reviews"},
    "bot": {"name", "account", "app", "persona", "budget_usd", "machine"},
    "guard": {"machine", "project", "persona", "apps"},
    "person": {"role", "projects"},
    "label": {"color", "description"},
}


@dataclass
class Finding:
    level: str
    where: str
    message: str

    def __str__(self):
        return f"{self.level}: {self.where}: {self.message}"


@dataclass
class Bot:
    name: str
    kind: str          # plan | dev
    project: str
    account: str
    machine: str | None
    persona: str | None


def load(path):
    """Returns (text, document). Raises on an unreadable or unparsable file."""
    with open(path, "rb") as f:
        text = f.read()
    return text.decode("utf-8"), tomllib.loads(text.decode("utf-8"))


def bots(doc):
    """Every project bot in the file, in file order."""
    out = []
    for pname, p in (doc.get("projects") or {}).items():
        if not isinstance(p, dict):
            continue
        for kind in ("plan", "dev"):
            b = p.get(kind)
            if isinstance(b, dict) and isinstance(b.get("name"), str):
                out.append(Bot(b["name"], kind, pname, b.get("account") or b["name"],
                               b.get("machine") or p.get("machine"), b.get("persona")))
    return out


def check(text, doc):
    """The checks. Returns a list of Finding."""
    f = []
    err = lambda where, msg: f.append(Finding("error", where, msg))
    warn = lambda where, msg: f.append(Finding("warn", where, msg))

    def keys(where, table, allowed):
        for k in table:
            if k not in allowed and not (allowed is KEYS["project"] and k.startswith("test_extra_")
                                         and k.removeprefix("test_extra_") in (doc.get("stacks") or {})):
                warn(where, f"unknown key `{k}`")

    def tables(name):
        t = doc.get(name) or {}
        if not isinstance(t, dict):
            err(name, "must be a table")
            return {}
        return {k: v for k, v in t.items() if isinstance(v, dict)} if name != "fleet" else t

    for lineno, line in enumerate(text.splitlines(), 1):
        if SECRET.search(line):
            err(f"line {lineno}", "looks like a secret; keys and tokens never go in this file")

    fleet = doc.get("fleet")
    if not isinstance(fleet, dict):
        err("fleet", "missing [fleet] table")
        fleet = {}
    keys("fleet", {k: v for k, v in fleet.items() if k not in ("labels", "bot_label")}, KEYS["fleet"])
    owner = fleet.get("owner")
    if not isinstance(owner, str) or not LOGIN.match(owner):
        err("fleet.owner", "a GitHub login is required")
        owner = None
    if not isinstance(fleet.get("name"), str):
        err("fleet.name", "a name is required")
    for k in ("labels", "bot_label"):
        if k in fleet:
            warn(f"fleet.{k}", "moved to the [labels] table (one entry per label with color and description); ignored")

    labels = doc.get("labels")
    if labels is not None:
        if not isinstance(labels, dict) or not labels:
            err("labels", "must be a table with one entry per label")
            labels = {}
        bot_labels = [n for n in labels if "{name}" in n]
        if len(bot_labels) > 1:
            err("labels", f"only one label may hold {{name}} (the bot label): {', '.join(bot_labels)}")
        for lname, spec in labels.items():
            w = f"labels.{lname}"
            if not isinstance(spec, dict):
                err(w, "must be a table with color and description")
                continue
            keys(w, spec, KEYS["label"])
            if not isinstance(spec.get("color"), str) or not COLOR.match(spec["color"]):
                err(w, "color must be six hex digits")
            if "description" in spec and not isinstance(spec["description"], str):
                err(w, "description must be a string")
            elif len(spec.get("description", "")) > 100:
                err(w, "description longer than GitHub's 100 characters")

    for d, allowed in (("round", KEYS["defaults.round"]), ("review", KEYS["defaults.review"]), ("page", KEYS["defaults.page"])):
        t = (doc.get("defaults") or {}).get(d)
        if isinstance(t, dict):
            keys(f"defaults.{d}", t, allowed)

    machines = tables("machines")
    if not machines:
        err("machines", "at least one [machines.<name>] is required")
    bots_machines = []
    for mname, m in machines.items():
        w = f"machines.{mname}"
        keys(w, m, KEYS["machine"])
        if m.get("role") not in ROLES:
            err(w, f"role must be one of {', '.join(ROLES)}")
        elif m["role"] == "bots":
            bots_machines.append(mname)
        if m.get("family") not in FAMILIES:
            err(w, f"family must be one of {', '.join(FAMILIES)}")
        if not isinstance(m.get("home_root"), str):
            err(w, "home_root is required")
        c = m.get("courier")
        if c is not None:
            if not isinstance(c, dict) or c.get("to") not in machines:
                err(w, "courier.to must name another machine")
            elif c.get("to") == mname:
                err(w, "courier.to must name another machine, not itself")

    stacks = tables("stacks")
    for sname, s in stacks.items():
        w = f"stacks.{sname}"
        keys(w, s, KEYS["stack"])
        if not isinstance(s.get("test"), str) or not s["test"].strip():
            err(w, "test (the command a round runs) is required")
        if not isinstance(s.get("allow"), list) or not all(isinstance(a, str) for a in s.get("allow") or []):
            err(w, "allow must be a list of command patterns")
        if "env" in s and not (isinstance(s["env"], dict) and all(isinstance(v, str) for v in s["env"].values())):
            err(w, "env must be a table of strings")

    guards = tables("guards")
    guard_names = set(guards)
    for gname, g in guards.items():
        w = f"guards.{gname}"
        keys(w, g, KEYS["guard"])
        if g.get("machine") not in machines:
            err(w, "machine must name a machine")
        if not isinstance(g.get("apps"), list) or not g["apps"]:
            err(w, "apps must list the guard's App slug (and a second App per foreign owner)")
        if "project" in g and g["project"] not in (doc.get("projects") or {}):
            err(w, f"project `{g['project']}` is not in [projects]")
    for mname, m in machines.items():
        if "guard" in m and m["guard"] not in guard_names:
            err(f"machines.{mname}", f"guard `{m['guard']}` is not in [guards]")

    projects = tables("projects")
    if not projects:
        err("projects", "at least one [projects.<name>] is required")
    foreign_apps = {a.get("owner") for g in guards.values() for a in (g.get("apps") or []) if isinstance(a, dict)}
    for pname, p in projects.items():
        w = f"projects.{pname}"
        keys(w, p, KEYS["project"])
        repo = p.get("repo")
        if not isinstance(repo, str) or not REPO.match(repo):
            err(w, "repo must be owner/name")
            repo = None
        for s in p.get("stacks") or []:
            if s not in stacks:
                err(w, f"stack `{s}` is not in [stacks]")
        if "ci" in p and p["ci"] not in CI:
            err(w, f"ci must be one of {', '.join(CI)}")
        for r in p.get("reads") or []:
            if r not in projects:
                err(w, f"reads names `{r}`, not a project")
        for login in p.get("ready_also") or []:
            if not isinstance(login, str) or not LOGIN.match(login):
                err(w, f"ready_also holds `{login}`, not a login")
        if "paused" in p and not isinstance(p["paused"], bool):
            err(w, "paused must be true or false")
        if "reviews" in p and (isinstance(p["reviews"], bool) or not isinstance(p["reviews"], int) or p["reviews"] < 0):
            err(w, "reviews must be a whole number: approving reviews the default branch's ruleset asks for")
        if "machine" in p and p["machine"] not in machines:
            err(w, f"machine `{p['machine']}` is not in [machines]")
        has_bots = any(isinstance(p.get(k), dict) for k in ("plan", "dev"))
        if has_bots:
            if not p.get("stacks") and not p.get("test"):
                err(w, "a project with bots needs stacks (or its own test line)")
            if repo and owner and repo.split("/")[0] != owner and not p.get("owner_app"):
                warn(w, f"repository under another owner than {owner}; a guard needs a second App "
                        f"and the project `owner_app` to read it")
            if p.get("owner_app") and repo and repo.split("/")[0] not in foreign_apps:
                warn(w, f"owner_app is set but no guard lists an App for owner {repo.split('/')[0]}")
            if not (isinstance(p.get("plan"), dict) and isinstance(p.get("dev"), dict)):
                warn(w, "a project usually has both a plan bot and a dev bot")
            if not bots_machines:
                err(w, "a project with bots needs a machine with role = \"bots\"")
        for kind in ("plan", "dev"):
            b = p.get(kind)
            if b is None:
                continue
            bw = f"{w}.{kind}"
            if not isinstance(b, dict):
                err(bw, "must be a table with at least name")
                continue
            keys(bw, b, KEYS["bot"])
            if not isinstance(b.get("name"), str) or not ACCOUNT.match(b["name"]):
                err(bw, "name must be a bot name usable as a Unix account (lowercase, digits, - and _)")
            if "account" in b and (not isinstance(b["account"], str) or not ACCOUNT.match(b["account"])):
                err(bw, "account must be a Unix account name")
            if "machine" in b and b["machine"] not in machines:
                err(bw, f"machine `{b['machine']}` is not in [machines]")
            if b.get("machine") is None and p.get("machine") is None and len(bots_machines) > 1:
                err(bw, f"several machines have role bots ({', '.join(bots_machines)}); say which one")

    seen_names, seen_accounts = {}, {}
    for b in bots(doc):
        if b.name in guard_names:
            err(f"projects.{b.project}.{b.kind}", f"`{b.name}` is also a guard")
        if b.name in seen_names:
            err(f"projects.{b.project}.{b.kind}", f"name `{b.name}` already used by projects.{seen_names[b.name]}")
        seen_names[b.name] = b.project
        key = (b.machine or (bots_machines[0] if bots_machines else None), b.account)
        if key in seen_accounts:
            err(f"projects.{b.project}.{b.kind}", f"account `{b.account}` already used by projects.{seen_accounts[key]}")
        seen_accounts[key] = b.project

    people = tables("people")
    owners = [l for l, p in people.items() if p.get("role") == "owner"]
    for login, p in people.items():
        w = f"people.{login}"
        keys(w, p, KEYS["person"])
        if not LOGIN.match(login):
            err(w, "not a GitHub login")
        if p.get("role") not in PEOPLE_ROLES:
            err(w, f"role must be one of {', '.join(PEOPLE_ROLES)}")
        for pr in p.get("projects") or []:
            if pr not in projects:
                err(w, f"projects names `{pr}`, not a project")
    if people and owner and owner not in owners:
        warn("people", f"fleet.owner {owner} is not listed with role = \"owner\"")
    return f


def summary(doc):
    b = bots(doc)
    projects = doc.get("projects") or {}
    with_bots = sum(1 for p in projects.values() if isinstance(p, dict) and (p.get("plan") or p.get("dev")))
    return (f"{len(doc.get('machines') or {})} machines, {len(doc.get('stacks') or {})} stacks, "
            f"{len(projects)} projects ({with_bots} with bots), {len(b)} bots, "
            f"{len(doc.get('guards') or {})} guards, {len(doc.get('people') or {})} people")
