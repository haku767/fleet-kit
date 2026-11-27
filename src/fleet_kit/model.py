"""The fleet's file: load it, check it.

A finding is (level, where, message); level is "error" or "warn". `check()` never raises on
a bad file, it returns findings; only an unreadable or unparsable file raises.
"""

import re
import tomllib
from dataclasses import dataclass

FAMILIES = ("arch", "nixos")
KINDS = ("session", "solo", "pair")   # a project's shape: no bots, a dev bot, a plan bot and a dev bot
ROLES = ("bots", "guard")
CI = ("yes", "no", "auto")
PEOPLE_ROLES = ("owner", "member", "tester")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]*$")
COLOR = re.compile(r"^[0-9a-fA-F]{6}$")
SET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
EVERY = re.compile(r"^\d+(ms|s|m|h|d|w)?$")
SETS = ("default", "minimal", "fantasy")   # the kit's sets (fleet_kit.sets.BUILTIN)


# What a secret looks like when it ends up in the file by mistake: GitHub tokens of every kind,
# an Anthropic key, a PEM block, a long hex or base64 run. Facts only, never a key.
SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[0-9a-f]{40,}\b|\b[A-Za-z0-9+/]{60,}={0,2}\b"
)

KEYS = {
    "fleet": {"name", "owner", "agents_dir", "trailers", "rules", "reviewer"},
    "reviewer": {"name", "account", "machine", "persona", "budget_usd", "uid"},
    "apps": {"dev", "review"},           # plus [apps.<name>] for a repository under another owner
    "app": {"owner", "slug"},
    "defaults.round": {"every", "max_worktrees", "max_turns", "budget_usd", "model_small", "model_large"},
    "defaults.review": {"every", "max_per_pr", "max_turns", "budget_usd", "model_small", "model_large"},
    "page": {"set", "sets", "collect_every", "labels"},   # plus one table per set, [page.<set>], its knobs
    "page.labels": {"ready", "next", "decide", "live"},
    "machine": {"role", "family", "home_root", "scratch_root", "page", "guard", "courier", "admin", "disks", "gpu"},
    "stack": {"test", "allow", "env", "note"},
    "project": {"repo", "stacks", "test", "test_extra", "test_note", "ci", "ci_note", "ci_checks", "review_note",
                "allow_extra", "env", "runner", "reads", "deploy", "paused", "owner_app", "ready_also", "plan", "dev", "machine",
                "reviews", "kind"},
    "bot": {"name", "account", "app", "persona", "budget_usd", "machine", "uid"},
    "guard": {"machine", "project", "persona", "apps", "uid"},
    "agent": {"machine", "account", "persona", "uid"},
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
    kind: str          # plan | dev | review (the fleet's reviewer, project "*")
    project: str
    account: str
    machine: str | None
    persona: str | None


def kind_of(p):
    """A project's kind: the `kind` key, else what its bots say (plan and dev: pair; dev only:
    solo; no bots: session)."""
    if isinstance(p.get("kind"), str):
        return p["kind"]
    has_plan = isinstance(p.get("plan"), dict)
    has_dev = isinstance(p.get("dev"), dict)
    return "pair" if has_plan and has_dev else "solo" if has_dev else "session"


def reviewer(doc):
    """The fleet's reviewer as a Bot (kind "review", project "*"), or None."""
    r = ((doc.get("fleet") or {}).get("reviewer"))
    if not isinstance(r, dict) or not isinstance(r.get("name"), str):
        return None
    return Bot(r["name"], "review", "*", r.get("account") or r["name"], r.get("machine"), r.get("persona"))


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

    for d, allowed in (("round", KEYS["defaults.round"]), ("review", KEYS["defaults.review"])):
        t = (doc.get("defaults") or {}).get(d)
        if isinstance(t, dict):
            keys(f"defaults.{d}", t, allowed)
    if isinstance((doc.get("defaults") or {}).get("page"), dict):
        warn("defaults.page", "moved to [page] (collect_every) and [page.fantasy] (the budgets); ignored")

    page = doc.get("page")
    if page is not None and not isinstance(page, dict):
        err("page", "must be a table")
        page = None
    if page:
        keys("page", {k: v for k, v in page.items() if not isinstance(v, dict) or k == "labels"}, KEYS["page"])
        sets = page.get("sets")
        if sets is not None and not (isinstance(sets, list) and sets and all(isinstance(x, str) and SET_NAME.match(x) for x in sets)):
            err("page.sets", "must be a list of set names (letters, digits, - and _)")
            sets = None
        chosen = page.get("set")
        if chosen is not None and not (isinstance(chosen, str) and SET_NAME.match(chosen)):
            err("page.set", "must be a set name (letters, digits, - and _)")
        elif chosen and sets and chosen not in sets:
            err("page.set", f"`{chosen}` is not in page.sets")
        if "collect_every" in page and not (isinstance(page["collect_every"], str) and EVERY.match(page["collect_every"])):
            err("page.collect_every", "a systemd span like 5m or 1h")
        lab = page.get("labels")
        if lab is not None:
            if not isinstance(lab, dict):
                err("page.labels", "must be a table: role = label name")
            else:
                keys("page.labels", lab, KEYS["page.labels"])
                for k, v in lab.items():
                    if not isinstance(v, str) or not v:
                        err(f"page.labels.{k}", "a label name")
        known = set(sets or []) | set(SETS)
        for k, v in page.items():
            if isinstance(v, dict) and k != "labels" and k not in known:
                warn(f"page.{k}", "knobs for a set that is neither the kit's nor in page.sets")

    machines = tables("machines")
    if not machines:
        err("machines", "at least one [machines.<name>] is required")
    bots_machines = []
    for mname, m in machines.items():
        w = f"machines.{mname}"
        keys(w, m, KEYS["machine"])
        if "disks" in m and not (isinstance(m["disks"], list) and all(isinstance(x, str) and x.startswith("/") for x in m["disks"])):
            err(f"{w}.disks", "a list of absolute paths")
        if "gpu" in m and not (isinstance(m["gpu"], bool) or (isinstance(m["gpu"], dict) and set(m["gpu"]) <= {"lock"})):
            err(f"{w}.gpu", "true, false, or { lock = \"/path\" }")
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

    # [apps]: the fleet's two Apps (dev writes, review plans, comments and approves) and, as
    # [apps.<name>], one per repository owner that is not the fleet's owner
    apps = doc.get("apps")
    if apps is not None:
        if not isinstance(apps, dict):
            err("apps", "must be a table: dev = \"<slug>\", review = \"<slug>\", [apps.<name>] owner and slug")
            apps = {}
        keys("apps", {k: v for k, v in apps.items() if not isinstance(v, dict)}, KEYS["apps"])
        for k in ("dev", "review"):
            if k in apps and (not isinstance(apps[k], str) or not LOGIN.match(apps[k])):
                err(f"apps.{k}", "must be the App's slug")
        for aname, a in apps.items():
            if isinstance(a, dict):
                keys(f"apps.{aname}", a, KEYS["app"])
                if not isinstance(a.get("owner"), str) or not LOGIN.match(a["owner"]):
                    err(f"apps.{aname}", "owner must be a GitHub login")
                if not isinstance(a.get("slug"), str) or not LOGIN.match(a["slug"]):
                    err(f"apps.{aname}", "slug must be the App's slug")
    # [fleet] reviewer: one account that reviews every project's pull requests
    rv = fleet.get("reviewer")
    if rv is not None:
        if not isinstance(rv, dict) or not isinstance(rv.get("name"), str) or not ACCOUNT.match(rv["name"]):
            err("fleet.reviewer", "must be a table with at least name (a Unix account name)")
            rv = None
        else:
            keys("fleet.reviewer", rv, KEYS["reviewer"])
            if "account" in rv and (not isinstance(rv["account"], str) or not ACCOUNT.match(rv["account"])):
                err("fleet.reviewer", "account must be a Unix account name")
            if rv.get("machine") not in (doc.get("machines") or {}):
                err("fleet.reviewer", "machine must name a machine with role bots")
            if "uid" in rv and (isinstance(rv["uid"], bool) or not isinstance(rv["uid"], int) or rv["uid"] < 1000):
                err("fleet.reviewer", "uid must be a whole number from 1000")

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
        if "uid" in g and (isinstance(g["uid"], bool) or not isinstance(g["uid"], int) or g["uid"] < 1000):
            err(w, "uid must be a whole number from 1000")
    # [agents.<name>]: a person's own session with an inbox of its own (no rounds, no App): machine
    # says where it runs, account the login it runs in, the inbox's owner there; an account equal to
    # the agent's name is a service account of its own, which `apply accounts` makes. The courier carries its mail like a bot's; elsewhere the courier guard owns it.
    agents = tables("agents")
    bot_names = {x.name for x in bots(doc)}
    for aname, a in agents.items():
        w = f"agents.{aname}"
        keys(w, a, KEYS["agent"])
        if a.get("machine") not in machines:
            err(w, "machine must name a machine")
        if not isinstance(a.get("account"), str) or not a["account"]:
            err(w, "account must name the login the session runs in (the inbox's owner there)")
        if aname in bot_names or aname in guard_names:
            err(w, "the name is a bot's or a guard's already")
        if "uid" in a and (isinstance(a["uid"], bool) or not isinstance(a["uid"], int) or a["uid"] < 1000):
            err(w, "uid must be a whole number from 1000")
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
        kind = p.get("kind")
        if kind is not None:
            if kind not in KINDS:
                err(w, f"kind must be one of {', '.join(KINDS)}")
            elif kind == "session" and has_bots:
                err(w, "kind session has no bots: drop plan and dev, or choose solo or pair")
            elif kind == "solo" and (not isinstance(p.get("dev"), dict) or isinstance(p.get("plan"), dict)):
                err(w, "kind solo has a dev bot and no plan bot")
            elif kind == "pair" and not (isinstance(p.get("plan"), dict) and isinstance(p.get("dev"), dict)):
                err(w, "kind pair has a plan bot and a dev bot")
        if has_bots:
            if not p.get("stacks") and not p.get("test"):
                err(w, "a project with bots needs stacks (or its own test line)")
            if repo and owner and repo.split("/")[0] != owner and not p.get("owner_app"):
                warn(w, f"repository under another owner than {owner}; a guard needs a second App "
                        f"and the project `owner_app` to read it")
            if p.get("owner_app") and repo and repo.split("/")[0] not in foreign_apps:
                warn(w, f"owner_app is set but no guard lists an App for owner {repo.split('/')[0]}")
            if kind_of(p) == "pair" and not (isinstance(p.get("plan"), dict) and isinstance(p.get("dev"), dict)):
                warn(w, "a project usually has both a plan bot and a dev bot")
            if isinstance(p.get("dev"), dict) and not isinstance(p.get("plan"), dict) and rv is None:
                warn(w, "a dev bot without a plan bot: nobody reviews its pull requests unless [fleet] names a reviewer")
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
            if "uid" in b and (isinstance(b["uid"], bool) or not isinstance(b["uid"], int) or b["uid"] < 1000):
                err(bw, "uid must be a whole number from 1000")
            if b.get("machine") is None and p.get("machine") is None and len(bots_machines) > 1:
                err(bw, f"several machines have role bots ({', '.join(bots_machines)}); say which one")

    seen_names, seen_accounts = {}, {}
    r = reviewer(doc)
    if r is not None:
        if r.name in guard_names or r.name in (doc.get("agents") or {}):
            err("fleet.reviewer", f"`{r.name}` is also a guard's or an agent's name")
        seen_names[r.name] = "the reviewer"
        seen_accounts[(r.machine, r.account)] = "the reviewer"
    uids = {}
    for where, t in ([(f"projects.{b.project}.{b.kind}", (doc["projects"][b.project].get(b.kind) or {})) for b in bots(doc)]
                     + [(f"guards.{g}", gv) for g, gv in guards.items()] + [(f"agents.{a}", av) for a, av in agents.items()]
                     + ([("fleet.reviewer", fleet.get("reviewer"))] if r is not None else [])):
        u = t.get("uid") if isinstance(t, dict) else None
        if isinstance(u, int) and not isinstance(u, bool):
            if u in uids:
                err(where, f"uid {u} is {uids[u]}'s already")
            uids[u] = where
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
            f"{len(projects)} projects ({with_bots} with bots), {len(b)} bots"
            + (f" and a reviewer ({reviewer(doc).name})" if reviewer(doc) else "") + ", "
            f"{len(doc.get('guards') or {})} guards, {len(doc.get('people') or {})} people"
            + (f", {len(doc['agents'])} agents" if doc.get("agents") else ""))
