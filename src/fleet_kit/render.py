"""What the tool derives from the file.

`render(doc)` returns {relative path: text}; the command writes them under --out. The files:

- `bots/<bot>.conf`: one per project bot, POSIX sh, sourced by bot-round (kind=dev) and
  bot-review (kind=plan) in place of their case blocks; the same variable names as those.
- `machines/<machine>/lists.sh`: the account lists the install scripts hold (`bots`, `ssh_bots`,
  `round_bots`, `review_bots`, `paused_bots`) for every machine with role bots.
- `machines/<machine>/courier.sh`: the courier's lists (`to_bots`, `from_bots`) for a machine
  with a courier.
- `tables.json`: the page's tables (projects, bots, owner_app), the shape fleet-collect holds.
- `rules-table.md`: the house rules' project table.
"""

import json
import re

# every round gets these besides git and gh: read-only looks around the worktree
COMMON_ALLOW = ["bash -n *", "sh -n *", "find *", "ls *", "cat *", "head *", "tail *", "wc *", "grep *",
                "rg *", "diff *", "sed -n *", "printf *", "echo *", "command -v *", "which *", "mkdir *"]
CI_SENTENCE = {
    "no": "This repository has no CI: write into the pull request body which checks you ran and that they passed",
    "auto": "While the repository has no CI, write into the pull request body which checks you ran and that they passed",
    "yes": None,
}
HEADER = "# /etc/fleet/bots/{name}.conf: written by `fleet apply conf` from fleet.toml ({fleet}); do not edit\n"


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
        self.bots_machines = [m for m, v in self.machines.items() if v.get("role") == "bots"]

    def app(self, bot):
        return bot.get("app") or f"{self.owner}-{bot['name']}"

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
        main, addons = [], []
        for s in p.get("stacks") or []:
            st = self.stacks.get(s) or {}
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
            if p.get("test_extra"):
                text += "; " + p["test_extra"]
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
        who = [self.owner]
        plan = p.get("plan")
        if isinstance(plan, dict) and plan.get("name"):
            who.append(f"{self.app(plan)}[bot]")
        who += p.get("ready_also") or []
        return " ".join(who)

    def ci_checks(self, p):
        if p.get("ci_checks"):
            return p["ci_checks"]
        main, _ = self.project_stacks(p)
        return p.get("test") or "; ".join(st["test"] for st in main if st.get("test")) or "the repository's checks"


def conf(fl, pname, p, kind, b, account, machine):
    repo = p.get("repo", "")
    home, _ = fl.paths(machine, account)
    lines = [HEADER.format(name=b["name"], fleet=fl.fleet.get("name", "")).rstrip("\n")]
    common = [("kind", kind), ("project", pname), ("name", b["name"]), ("account", account),
              ("machine", machine or ""), ("paused", "yes" if p.get("paused") else "no"), ("repo", repo),
              ("dir", f"{home}/{repo.rsplit('/', 1)[-1]}")]
    for k, v in common:
        lines.append(f"{k}={q(v)}")
    if kind == "dev":
        lines += [f"app={q(fl.app(b))}", f"trusted={q(fl.trusted(p))}", f"test_cmd={q(fl.test_cmd(p))}",
                  f"test_cmd_nix={q(fl.test_cmd_addon(p))}", f"allow={q(fl.allow(p))}"]
        env = fl.env(p, machine, account)
        if env:
            lines.append("export " + " ".join(f"{k}={q(v)}" for k, v in env.items()))
    else:
        dev = p.get("dev") if isinstance(p.get("dev"), dict) else None
        lines += [f"app={q(fl.app(dev) if dev else '')}", f"reviewer={q(fl.app(b) + '[bot]')}",
                  f"ci={q(p.get('ci', 'yes'))}", f"ci_checks={q(fl.ci_checks(p))}",
                  f"review_note={q(p.get('review_note', ''))}"]
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
        out[f"machines/{mname}/lists.sh"] = (
            f"# the account lists for {mname}, rendered by fleet from fleet.toml ({fl.fleet.get('name', '')}); do not edit\n"
            f"bots={q(' '.join(accounts + ssh))}\nssh_bots={q(' '.join(ssh))}\nround_bots={q(' '.join(rounds))}\n"
            f"review_bots={q(' '.join(reviews))}\npaused_bots={q(' '.join(paused))}\n")
    for mname, m in fl.machines.items():
        c = m.get("courier")
        if not c:
            continue
        to = c["to"]
        to_names = [g for g, gv in fl.guards.items() if gv.get("machine") == to] + \
                   [b["name"] for *_, b, _a, bm in all_bots if bm == to]
        from_names = [g for g, gv in fl.guards.items() if gv.get("machine") == mname] + \
                     [b["name"] for *_, b, _a, bm in all_bots if bm == mname]
        out[f"machines/{mname}/courier.sh"] = (
            f"# the courier's lists on {mname} (inbox names, the register's), rendered by fleet from fleet.toml; do not edit\n"
            f"to_host={q(to)}\nto_ssh={q(c.get('ssh', ''))}\nevery={q(c.get('every', '15m'))}\n"
            f"to_bots={q(' '.join(to_names))}\nfrom_bots={q(' '.join(from_names))}\n")
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
    for g, gv in fl.guards.items():
        bots.append([g, "guard", "", gv.get("machine"), "none", g])
    owner_app = {}
    for gv in fl.guards.values():
        for a in gv.get("apps") or []:
            if isinstance(a, dict):
                owner_app[a["owner"]] = a["name"]
    return json.dumps({"projects": projects, "bots": bots, "paused": paused, "owner_app": owner_app}, indent=1) + "\n"


def rules_table(fl):
    rows = ["| Project | Plan bot | Dev bot | Repository |", "|---|---|---|---|"]
    for pname, p in fl.projects.items():
        plan, dev = p.get("plan"), p.get("dev")
        if not (isinstance(plan, dict) or isinstance(dev, dict)):
            continue
        rows.append(f"| {pname}{' (paused)' if p.get('paused') else ''} | {plan.get('name', '') if isinstance(plan, dict) else ''} "
                    f"| {dev.get('name', '') if isinstance(dev, dict) else ''} | {p.get('repo', '')} |")
    return "\n".join(rows) + "\n"


def render(doc):
    fl = Fleet(doc)
    out = {}
    for pname, p, kind, b, account, machine in fl.bots():
        out[f"bots/{b['name']}.conf"] = conf(fl, pname, p, kind, b, account, machine)
    out.update(lists(fl))
    out["tables.json"] = tables(fl)
    out["rules-table.md"] = rules_table(fl)
    return out
