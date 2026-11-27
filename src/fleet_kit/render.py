"""What the tool derives from the file.

`render(doc)` returns {relative path: text}; the command writes them under --out. The files:

- `bots/<account>.conf`: one per project bot, named after its Unix account (what a script
  knows as `$me`; `name=` inside is the register name), POSIX sh, sourced by bot-round
  (kind=dev) and bot-review (kind=plan) in place of their case blocks; the same variable
  names as those. `bots/<reviewer>.<project>.conf` (kind=review): what the fleet's reviewer
  reads for one project (`bot-review --project <project>`).
- `machines/<machine>/fleet.json`: the machine's part of the fleet as data (accounts with
  their uids and kinds, the timers, the courier's lists), for a machine whose configuration
  is code and reads the fleet from one file instead of restating it.
- `machines/<machine>/lists.sh`: the account lists the install scripts hold (`bots`, `ssh_bots`,
  `round_bots`, `review_bots`, `paused_bots`) for every machine with role bots.
- `machines/<machine>/courier.sh`: the courier's lists (`to_bots`, `from_bots`) for a machine
  with a courier.
- `tables.json`: the page's tables (projects, bots, owner_app), the shape fleet-collect holds.
- `rules-table.md`: the house rules' project table.
"""

import json
import re

from . import model

# every round gets these besides git and gh: read-only looks around the worktree
COMMON_ALLOW = ["bash -n *", "sh -n *", "find *", "ls *", "cat *", "head *", "tail *", "wc *", "grep *",
                "rg *", "diff *", "sed -n *", "printf *", "echo *", "command -v *", "which *", "mkdir *"]
CI_SENTENCE = {
    "no": "This repository has no CI: write into the pull request body which checks you ran and that they passed",
    "auto": "While the repository has no CI, write into the pull request body which checks you ran and that they passed",
    "yes": None,
}
HEADER = "# /etc/fleet/bots/{account}.conf: written by `fleet apply conf` from fleet.toml ({fleet}); do not edit\n"


def q(s):
    """Single-quote for sh."""
    return "'" + str(s).replace("'", "'\\''") + "'"


def split_commands(test):
    return [c.strip() for c in re.split(r"\s*(?:&&|;)\s*", test) if c.strip()]


class Fleet:
    """The file, resolved: machines, projects, bots with their defaults filled in."""

    def __init__(self, doc):
        self.doc = doc
        self.fleet = doc.get("fleet") or {}
        self.owner = self.fleet.get("owner", "")
        self.machines = doc.get("machines") or {}
        self.stacks = doc.get("stacks") or {}
        self.projects = doc.get("projects") or {}
        self.guards = doc.get("guards") or {}
        self.agents = doc.get("agents") or {}   # a person's own sessions with an inbox
        self.apps = doc.get("apps") or {}       # the fleet's two Apps (dev, review) and [apps.<name>] per foreign owner
        self.reviewer = model.reviewer(doc)     # the fleet's reviewer (a Bot of kind review), or None
        self.bots_machines = [m for m, v in self.machines.items() if v.get("role") == "bots"]

    def app(self, bot, kind="dev"):
        """A bot's App slug: its own `app`, else the fleet's App for its kind ([apps] dev for a
        dev bot, review for a plan bot and the reviewer), else `<owner>-<name>`."""
        if bot.get("app"):
            return bot["app"]
        fleet_app = self.apps.get("review" if kind in ("plan", "review") else "dev")
        return fleet_app if isinstance(fleet_app, str) else f"{self.owner}-{bot['name']}"

    def reviewer_dict(self):
        return (self.fleet.get("reviewer") or {}) if self.reviewer else {}

    def reviewed(self):
        """(project name, project) for every project the fleet's reviewer reviews: with a dev
        bot, not paused."""
        if not self.reviewer:
            return []
        return [(pn, p) for pn, p in self.projects.items() if isinstance(p.get("dev"), dict) and not p.get("paused")]

    def machine_of(self, project, bot):
        return bot.get("machine") or project.get("machine") or (self.bots_machines[0] if self.bots_machines else None)

    def bots(self):
        """(project name, project, kind, bot dict, account, machine) for every project bot."""
        for pname, p in self.projects.items():
            for kind in ("plan", "dev"):
                b = p.get(kind)
                if isinstance(b, dict) and b.get("name"):
                    yield pname, p, kind, b, b.get("account") or b["name"], self.machine_of(p, b)

    def paths(self, machine, account):
        m = self.machines.get(machine) or {}
        home = f"{m.get('home_root', '/srv')}/{account}"
        scratch = f"{m['scratch_root']}/{account}" if m.get("scratch_root") else home
        return home, scratch

    def project_stacks(self, p):
        """(main stacks, add-on stacks); an add-on's test holds "{each}" and wraps the others' commands.
        Each stack dict gets its name under "_name" so the add-on can look up test_extra_<name>."""
        main, addons = [], []
        for s in p.get("stacks") or []:
            st = dict(self.stacks.get(s) or {})
            st["_name"] = s
            (addons if "{each}" in (st.get("test") or "") else main).append(st)
        return main, addons

    def test_cmd(self, p):
        main, addons = self.project_stacks(p)
        base = p.get("test") or "; ".join(st["test"] for st in main if st.get("test"))
        parts = [base] if base else []
        if p.get("test_extra"):
            parts.append(p["test_extra"])
        cmd = "; ".join(parts)
        sentences = [cmd] if cmd else []
        ci = p.get("ci_note") or CI_SENTENCE.get(p.get("ci", "yes"))
        if ci:
            sentences.append(ci)
        if p.get("test_note"):
            sentences.append(p["test_note"])
        return ". ".join(x.rstrip(".") for x in sentences)   # "uv run ruff format --check ." keeps one dot

    def test_cmd_addon(self, p):
        """The add-on stacks' version: every command of the base wrapped, one per call."""
        main, addons = self.project_stacks(p)
        if not addons:
            return ""
        base = p.get("test") or "; ".join(st["test"] for st in main if st.get("test"))
        out = []
        for st in addons:
            cmds = [st["test"].replace("{each}", c) for c in split_commands(base)]
            text = "inside the repository's dev shell, one per call: " + "; ".join(cmds)
            extra = p.get(f"test_extra_{st['_name']}", p.get("test_extra"))   # the add-on's own wording when given
            if extra:
                text += "; " + extra
            if st.get("note"):
                text += "; " + st["note"]
            out.append(text)
        return ". ".join(out)

    def allow(self, p):
        main, addons = self.project_stacks(p)
        items = ["git *", "gh *"]
        for st in main + addons:
            items += st.get("allow") or []
        items += p.get("allow_extra") or []
        items += COMMON_ALLOW
        seen, out = set(), []
        for i in items:
            if i not in seen:
                seen.add(i)
                out.append(f"Bash({i})")
        return " ".join(out)

    def env(self, p, machine, account):
        if "env" in p:
            env = dict(p["env"])
        else:
            env = {}
            main, addons = self.project_stacks(p)
            for st in main + addons:
                env.update(st.get("env") or {})
        home, scratch = self.paths(machine, account)
        return {k: v.replace("{scratch}", scratch).replace("{home}", home) for k, v in env.items()}

    def trusted(self, p):
        """Who may set `ready`: the owner, the fleet's reviewer, the project's plan bot, `ready_also`."""
        who = [self.owner]
        if self.reviewer:
            who.append(f"{self.app(self.reviewer_dict(), 'review')}[bot]")
        plan = p.get("plan")
        if isinstance(plan, dict) and plan.get("name"):
            app = f"{self.app(plan, 'plan')}[bot]"
            if app not in who:
                who.append(app)
        who += p.get("ready_also") or []
        return " ".join(who)

    def ci_checks(self, p):
        if p.get("ci_checks"):
            return p["ci_checks"]
        main, _ = self.project_stacks(p)
        return p.get("test") or "; ".join(st["test"] for st in main if st.get("test")) or "the repository's checks"


# what the rounds ran with before the file carried the values (haku's fleet); bot-round and
# bot-review fall back to the same when a line is missing
ROUND_DEFAULTS = {"every": "20m", "max_worktrees": 2, "max_turns": 150, "budget_usd": 10,
                  "model_small": "claude-sonnet-5-5", "model_large": "claude-opus-5-5"}
REVIEW_DEFAULTS = {"every": "20m", "max_per_pr": 3, "max_turns": 60, "budget_usd": 5,
                   "model_small": "claude-sonnet-5-5", "model_large": "claude-opus-5-5"}


def conf(fl, pname, p, kind, b, account, machine):
    repo = p.get("repo", "")
    home, _ = fl.paths(machine, account)
    m = fl.machines.get(machine) or {}
    lines = [HEADER.format(account=f"{account}.{pname}" if kind == "review" else account, fleet=fl.fleet.get("name", "")).rstrip("\n")]
    common = [("kind", kind), ("project", pname), ("name", b["name"]), ("account", account),
              ("machine", machine or ""), ("paused", "yes" if p.get("paused") else "no"), ("repo", repo),
              ("dir", f"{home}/{repo.rsplit('/', 1)[-1]}"),
              # the fleet around the bot: who decides, who gets the round's notes, where the
              # inboxes and status files are (bot-round, bot-review)
              ("owner", fl.owner), ("guard", m.get("guard", "")), ("agents_dir", fl.fleet.get("agents_dir", "/srv/agents"))]
    for k, v in common:
        lines.append(f"{k}={q(v)}")
    defaults = fl.doc.get("defaults") or {}
    if kind == "dev":
        lines += [f"app={q(fl.app(b, 'dev'))}", f"trusted={q(fl.trusted(p))}", f"test_cmd={q(fl.test_cmd(p))}",
                  f"test_cmd_nix={q(fl.test_cmd_addon(p))}", f"allow={q(fl.allow(p))}"]
        knobs = dict(ROUND_DEFAULTS, **(defaults.get("round") or {}))
    else:   # plan (its own review rounds) or review (the fleet's reviewer, one conf per project)
        dev = p.get("dev") if isinstance(p.get("dev"), dict) else None
        lines += [f"app={q(fl.app(dev, 'dev') if dev else '')}", f"reviewer={q(fl.app(b, kind) + '[bot]')}",
                  f"ci={q(p.get('ci', 'yes'))}", f"ci_checks={q(fl.ci_checks(p))}",
                  f"review_note={q(p.get('review_note', ''))}"]
        knobs = dict(REVIEW_DEFAULTS, **(defaults.get("review") or {}))
    if b.get("budget_usd") is not None:
        knobs["budget_usd"] = b["budget_usd"]
    # [defaults.round] / [defaults.review], a bot's own budget over them: the round's knobs
    lines += [f"{k}={q(knobs[k])}" for k in (ROUND_DEFAULTS if kind == "dev" else REVIEW_DEFAULTS)]
    if kind == "dev":
        env = fl.env(p, machine, account)
        if env:
            lines.append("export " + " ".join(f"{k}={q(v)}" for k, v in env.items()))
    return "\n".join(lines) + "\n"


def lists(fl):
    out = {}
    all_bots = list(fl.bots())
    for mname in fl.bots_machines:
        mine = [x for x in all_bots if x[5] == mname]
        accounts = [a for *_, a, _m in mine]
        # the guards of other machines whose courier delivers here get an account with ssh
        ssh = [g for g, gv in fl.guards.items()
               if (fl.machines.get(gv.get("machine")) or {}).get("courier", {}).get("to") == mname]
        rounds = [a for _, p, kind, _, a, _ in mine if kind == "dev"]
        reviews = [a for _, p, kind, _, a, _ in mine if kind == "plan"]
        paused = [a for _, p, kind, _, a, _ in mine if p.get("paused")]
        rv = fl.reviewer if fl.reviewer and fl.reviewer.machine == mname else None
        if rv:
            accounts.append(rv.account)
        out[f"machines/{mname}/lists.sh"] = (
            f"# /etc/fleet/lists.sh: written by `fleet apply lists` from fleet.toml ({fl.fleet.get('name', '')}) for {mname}; do not edit\n"
            f"bots={q(' '.join(accounts + ssh))}\nssh_bots={q(' '.join(ssh))}\nround_bots={q(' '.join(rounds))}\n"
            f"review_bots={q(' '.join(reviews))}\npaused_bots={q(' '.join(paused))}\n"
            f"reviewer={q(rv.account if rv else '')}\nreviewer_projects={q(' '.join(pn for pn, _ in fl.reviewed()) if rv else '')}\n")
    for mname, m in fl.machines.items():
        c = m.get("courier")
        if not c:
            continue
        to = c["to"]
        rv_on = lambda m: [fl.reviewer.name] if fl.reviewer and fl.reviewer.machine == m else []
        to_names = [g for g, gv in fl.guards.items() if gv.get("machine") == to] + \
                   [b["name"] for *_, b, _a, bm in all_bots if bm == to] + rv_on(to) + \
                   [a for a, av in fl.agents.items() if av.get("machine") == to]
        from_names = [g for g, gv in fl.guards.items() if gv.get("machine") == mname] + \
                     [b["name"] for *_, b, _a, bm in all_bots if bm == mname] + rv_on(mname) + \
                     [a for a, av in fl.agents.items() if av.get("machine") == mname]
        # from_agents: the [agents] on this machine with the login that owns each inbox here
        # (name=account), for a machine whose module declares the inboxes (NixOS)
        from_agents = [f"{a}={av.get('account', '')}" for a, av in fl.agents.items() if av.get("machine") == mname]
        out[f"machines/{mname}/courier.sh"] = (
            f"# /etc/fleet/courier.sh: written by `fleet apply lists` from fleet.toml ({fl.fleet.get('name', '')}) for {mname}"
            f" (inbox names, the register's); do not edit\n"
            f"to_host={q(to)}\nto_ssh={q(c.get('ssh', ''))}\nevery={q(c.get('every', '15m'))}\n"
            f"to_bots={q(' '.join(to_names))}\nfrom_bots={q(' '.join(from_names))}\n"
            f"from_agents={q(' '.join(from_agents))}\n")
    return out


def tables(fl):
    projects = [(p.get("repo", ""), p.get("repo", "").rsplit("/", 1)[-1]) for p in fl.projects.values()]
    bots = []
    paused = []
    for pname, p, kind, b, account, machine in fl.bots():
        rounds = "round" if kind == "dev" else "review"   # paused projects keep their kind: the timers are off, not gone
        bots.append([b["name"], kind, p.get("repo", "").rsplit("/", 1)[-1], machine, rounds, account])
        if p.get("paused") and pname not in paused:
            paused.append(pname)
    botless = [p.get("repo", "").rsplit("/", 1)[-1] for p in fl.projects.values()
               if not any(isinstance(p.get(k), dict) for k in ("plan", "dev"))]
    if fl.reviewer:
        bots.append([fl.reviewer.name, "review", "", fl.reviewer.machine, "review", fl.reviewer.account])
    for g, gv in fl.guards.items():   # a guard's project: its own, else the first without bots (as fleet-collect)
        proj = fl.projects.get(gv.get("project"), {}).get("repo", "").rsplit("/", 1)[-1] or (botless[0] if botless else "")
        bots.append([g, "guard", proj, gv.get("machine"), "none", g])
    owner_app = {}
    for gv in fl.guards.values():
        for a in gv.get("apps") or []:
            if isinstance(a, dict):
                owner_app[a["owner"]] = a["name"]
    return json.dumps({"projects": projects, "bots": bots, "paused": paused, "owner_app": owner_app}, indent=1) + "\n"


def rules_table(fl):
    rows = ["| Project | Kind | Plan bot | Dev bot | Repository |", "|---|---|---|---|---|"]
    for pname, p in fl.projects.items():
        plan, dev = p.get("plan"), p.get("dev")
        if not (isinstance(plan, dict) or isinstance(dev, dict)):
            continue
        rows.append(f"| {pname}{' (paused)' if p.get('paused') else ''} | {model.kind_of(p)} "
                    f"| {plan.get('name', '') if isinstance(plan, dict) else ''} "
                    f"| {dev.get('name', '') if isinstance(dev, dict) else ''} | {p.get('repo', '')} |")
    if fl.reviewer:
        rows.append(f"\nReviewer for every project: {fl.reviewer.name}.")
    return "\n".join(rows) + "\n"


def machine_json(fl, mname):
    """The machine's part of the fleet as data: every account that lives here with its uid (when
    the file says one), kind, project, home and scratch, whether it lingers and has an inbox; the
    timers to enable; the courier's lists. What a declarative machine reads."""
    m = fl.machines.get(mname) or {}
    accounts, timers = [], []
    for pname, p, kind, b, acct, bm in fl.bots():
        if bm != mname:
            continue
        home, scratch = fl.paths(mname, acct)
        accounts.append({"account": acct, "name": b["name"], "uid": b.get("uid"), "kind": kind, "project": pname,
                         "repo": p.get("repo", ""), "home": home, "scratch": scratch, "linger": True, "inbox": True,
                         "paused": bool(p.get("paused")), "app": fl.app(b, kind)})
        if not p.get("paused"):
            timers.append({"unit": f"bot-round@{acct}.timer" if kind == "dev" else f"bot-review@{acct}.timer",
                           "account": acct, "project": pname})
    if fl.reviewer and fl.reviewer.machine == mname:
        rv, r = fl.reviewer, fl.reviewer_dict()
        home, scratch = fl.paths(mname, rv.account)
        accounts.append({"account": rv.account, "name": rv.name, "uid": r.get("uid"), "kind": "review", "project": "*",
                         "repo": "", "home": home, "scratch": scratch, "linger": True, "inbox": True, "paused": False,
                         "app": fl.app(r, "review")})
        timers += [{"unit": f"bot-review-for@{pn}.timer", "account": rv.account, "project": pn} for pn, _ in fl.reviewed()]
    for g, gv in fl.guards.items():
        if gv.get("machine") == mname:
            home, scratch = fl.paths(mname, g)
            accounts.append({"account": g, "name": g, "uid": gv.get("uid"), "kind": "guard", "project": gv.get("project", ""),
                             "repo": "", "home": home, "scratch": scratch, "linger": True, "inbox": True, "paused": False,
                             "apps": gv.get("apps") or []})
    for a, av in fl.agents.items():
        if av.get("machine") == mname:
            own = av.get("account") == a
            home, scratch = fl.paths(mname, a) if own else (None, None)
            accounts.append({"account": av.get("account", ""), "name": a, "uid": av.get("uid") if own else None,
                             "kind": "agent", "project": "", "repo": "", "home": home, "scratch": scratch,
                             "linger": own, "inbox": True, "paused": False})
    courier = m.get("courier") or None
    lists_ = lists(fl)
    doc = {"fleet": fl.fleet.get("name", ""), "owner": fl.owner, "machine": mname, "family": m.get("family"),
           "home_root": m.get("home_root", "/srv"), "scratch_root": m.get("scratch_root"), "agents_dir": fl.fleet.get("agents_dir", "/srv/agents"),
           "admin": m.get("admin"), "guard": m.get("guard"), "apps": {k: v for k, v in fl.apps.items() if not isinstance(v, dict)},
           "foreign_apps": {k: v for k, v in fl.apps.items() if isinstance(v, dict)},
           "reviewer": fl.reviewer.account if fl.reviewer and fl.reviewer.machine == mname else None,
           "reviewed": [pn for pn, _ in fl.reviewed()] if fl.reviewer and fl.reviewer.machine == mname else [],
           "token_service": ({"account": "fleet", "socket": "/run/fleet/token.sock", "keys": "/var/lib/fleet/apps",
                              "slugs": sorted({v if isinstance(v, str) else v.get("slug", "") for v in fl.apps.values()})}
                             if fl.apps and m.get("role") == "bots" else None),
           "accounts": accounts, "timers": timers, "courier": courier,
           "courier_lists": lists_.get(f"machines/{mname}/courier.sh")}
    return json.dumps(doc, indent=1) + "\n"


def render(doc):
    fl = Fleet(doc)
    out = {}
    for pname, p, kind, b, account, machine in fl.bots():
        out[f"bots/{account}.conf"] = conf(fl, pname, p, kind, b, account, machine)
    if fl.reviewer:
        for pname, p in fl.reviewed():
            out[f"bots/{fl.reviewer.account}.{pname}.conf"] = conf(fl, pname, p, "review", fl.reviewer_dict(), fl.reviewer.account, fl.reviewer.machine)
    out.update(lists(fl))
    for mname in fl.machines:
        out[f"machines/{mname}/fleet.json"] = machine_json(fl, mname)
    out["tables.json"] = tables(fl)
    out["rules-table.md"] = rules_table(fl)
    return out


def apply_conf(files, directory, dry_run=False):
    """Puts the rendered bots/*.conf into `directory` (0755, files 0644), removes a .conf there
    that the render no longer has and that carries the header (never a hand-written file).
    Returns the list of (action, path): added, changed, removed, kept."""
    wanted = {rel.split("/", 1)[1]: text for rel, text in files.items() if rel.startswith("bots/")}
    return apply_files(wanted, directory, "written by `fleet apply conf`", (".conf",), dry_run)


def apply_lists(files, machine, directory, dry_run=False):
    """Puts this machine's lists.sh (a bots machine) and courier.sh (a machine with a courier)
    into `directory` as /etc/fleet/lists.sh and /etc/fleet/courier.sh; removes one of the two
    there when the render no longer has it for this machine. Same return as apply_conf."""
    wanted = {rel.rsplit("/", 1)[1]: text for rel, text in files.items()
              if rel.startswith(f"machines/{machine}/") and rel.endswith(".sh")}   # fleet.json is the render's, not /etc's
    return apply_files(wanted, directory, "written by `fleet apply lists`", ("lists.sh", "courier.sh"), dry_run)


def apply_files(wanted, directory, marker, names, dry_run):
    """Writes `wanted` (file name -> text) into `directory`, atomically, 0644; removes a file
    there whose name ends in one of `names`, carries `marker` in its first line and is not
    wanted any more. Returns the list of (action, path): added, changed, removed, kept."""
    import os
    import tempfile
    actions = []
    if not dry_run:
        os.makedirs(directory, mode=0o755, exist_ok=True)
    present = set(os.listdir(directory)) if os.path.isdir(directory) else set()
    for fname, text in wanted.items():
        path = os.path.join(directory, fname)
        old = None
        if fname in present:
            with open(path) as f:
                old = f.read()
        if old == text:
            actions.append(("kept", path))
            continue
        actions.append(("added" if old is None else "changed", path))
        if not dry_run:
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".fleet-")
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
    for fname in sorted(present - set(wanted)):
        path = os.path.join(directory, fname)
        if not fname.endswith(names) or not os.path.isfile(path):
            continue
        with open(path) as f:
            first = f.readline()
        if marker in first:
            actions.append(("removed", path))
            if not dry_run:
                os.remove(path)
    return actions
