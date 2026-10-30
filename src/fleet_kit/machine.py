"""`fleet doctor`, the machine half: what the guards' hourly checks look at on a bots machine.

One Finding per check (doctor.Finding), levels ok | warn | fail | skip: fail is what a round
trips on (no account, no `.conf`, a timer off, Claude not signed in), warn is what should be
put right but does not stop a round (a stale `.conf`, an old status file, a full inbox), skip is
what this caller cannot see (a bot's home needs root) or what does not apply (a NixOS machine
takes rendered files, not install-accounts.sh). Everything is read-only. `run(doc, machine)`
checks the machine named (default: this host); `root`, `systemctl`, `user_of` and `owner_of`
exist so the tests run in a temporary directory without root and without systemd. Nothing here
imports the GitHub side: a machine without `gh` still gets this half.

What stays with a machine's guard, not here: disks, the GPU, ports, backups, units outside the
fleet, big files in homes, the LOG (ws-guard-check on ws-msi)."""

import json
import os
import pwd
import stat
import subprocess
import time
from pathlib import Path

from . import render
from .doctor import Finding

CONF_DIR = "/etc/fleet/bots"
LISTS = "/etc/fleet/lists.sh"
SCRIPTS = ("/usr/local/bin/bot-round", "/usr/local/bin/bot-review", "/usr/local/bin/fleet-token")
UNITS = tuple(f"/etc/systemd/system/{u}" for u in
              ("bot-round@.service", "bot-round@.timer", "bot-review@.service", "bot-review@.timer"))
DEFAULTS_FILE = "/etc/default/bot-round"
LINGER_DIR = "/var/lib/systemd/linger"
RULES = "/etc/claude-code/CLAUDE.md"
ROUND_TIMEOUT_S = 2 * 3600 + 30 * 60   # bot-round@.service TimeoutStartSec=2h30m
INBOX_OLD_S = 3 * 24 * 3600
GUARD_REPORT_OLD_S = 2 * 3600


def seconds(every, default=20 * 60):
    """'20m', '15m', '1h', '90s' -> seconds; the default when the value is not one of those."""
    s = str(every or "").strip()
    try:
        if s.endswith("h"):
            return int(float(s[:-1]) * 3600)
        if s.endswith("m"):
            return int(float(s[:-1]) * 60)
        if s.endswith("s"):
            return int(float(s[:-1]))
        return int(s) if s else default
    except ValueError:
        return default


class Host:
    """Where the checks look: a root to prefix every path with, and the lookups the tests fake."""

    def __init__(self, root="/", systemctl=None, user_of=None, owner_of=None, now=None):
        self.root = Path(root)
        self._systemctl = systemctl
        self._user_of = user_of
        self._owner_of = owner_of
        self.now = now or time.time()

    def path(self, p):
        return self.root / str(p).lstrip("/")

    def user(self, name):
        """The account's home, or None when there is no such account."""
        if self._user_of is not None:
            return self._user_of(name)
        try:
            return pwd.getpwnam(name).pw_dir
        except KeyError:
            return None

    def owner(self, p):
        """The owner's name of a path under the root (its real owner, or what the test says)."""
        if self._owner_of is not None:
            return self._owner_of(str(p))
        try:
            return pwd.getpwuid(p.stat().st_uid).pw_name
        except KeyError:
            return str(p.stat().st_uid)

    def mode(self, p):
        return stat.S_IMODE(p.stat().st_mode)

    def group_or_other_writable(self, p):
        return bool(self.mode(p) & 0o022)

    def age(self, p):
        return self.now - p.stat().st_mtime

    def unit(self, name):
        """UnitFileState and ActiveState of a unit, as `systemctl show` prints them."""
        if self._systemctl is not None:
            out = self._systemctl(["show", name, "-p", "UnitFileState", "-p", "ActiveState", "--no-pager"])
        else:
            try:
                out = subprocess.run(["systemctl", "show", name, "-p", "UnitFileState", "-p", "ActiveState", "--no-pager"],
                                     capture_output=True, text=True, timeout=30).stdout
            except (OSError, subprocess.TimeoutExpired):
                out = ""
        d = {}
        for line in out.splitlines():
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
        return d


def mode_ok(host, p, root_only=True):
    """Root's and not writable by group or others: what the scripts demand of a fleet file."""
    return (not root_only or host.owner(p) == "root") and not host.group_or_other_writable(p)


def check_machine(doc, machine):
    m = (doc.get("machines") or {}).get(machine)
    if not m:
        return None, [Finding("skip", "machine", f"{machine} is not a machine in the file (--machine names one)")]
    if m.get("role") != "bots":
        return None, [Finding("skip", "machine", f"{machine} has role {m.get('role')}, not bots: nothing to check here")]
    return m, [Finding("ok", "machine", f"{machine}: role bots, family {m.get('family')}, homes under {m.get('home_root', '/srv')}")]


def check_account(host, fl, acct, machine, where):
    out = []
    home_wanted, _ = fl.paths(machine, acct)
    home = host.user(acct)
    if home is None:
        return [Finding("fail", where, f"no Unix account {acct} (install-accounts.sh bots, or fleet apply accounts)")]
    hp = host.path(home)
    if not hp.is_dir():
        return [Finding("fail", where, f"home {home} is missing")]
    if home != home_wanted:
        out.append(Finding("warn", where, f"home is {home}, the file says {home_wanted}"))
    problems = []
    if host.owner(hp) != acct:
        problems.append(f"home owned by {host.owner(hp)}")
    if host.mode(hp) & 0o027:
        problems.append(f"home mode {host.mode(hp):o}, 750 expected")
    if not host.path(f"{LINGER_DIR}/{acct}").exists():
        problems.append("linger off (loginctl enable-linger)")
    link = hp / "agents"
    if not link.is_symlink():
        problems.append("~/agents is not a link to the agents directory")
    elif os.readlink(link) != fl.fleet.get("agents_dir", "/srv/agents"):
        problems.append(f"~/agents points to {os.readlink(link)}")
    if problems:
        out.append(Finding("warn", where, "; ".join(problems)))
    else:
        out.append(Finding("ok", where, f"account, home {home} (750, linger, ~/agents)"))
    return out


def check_scratch(host, fl, acct, machine, where):
    _, scratch = fl.paths(machine, acct)
    p = host.path(scratch)
    if not p.is_dir():
        return Finding("fail", where, f"{scratch} is missing: the round's build output goes there (install-accounts.sh bots)")
    if host.owner(p) != acct:
        return Finding("warn", where, f"{scratch} is owned by {host.owner(p)}, not {acct}")
    return Finding("ok", where, f"{scratch} is {acct}'s")


def check_conf(host, rendered, acct, kind, where):
    p = host.path(f"{CONF_DIR}/{acct}.conf")
    if not p.is_file():
        return Finding("fail", where, f"{CONF_DIR}/{acct}.conf is missing: the round exits 2 (fleet apply conf, as root)")
    if not (mode_ok(host, p) and mode_ok(host, p.parent)):
        return Finding("fail", where, f"{CONF_DIR}/{acct}.conf: must be root's and not writable by others, "
                                      "like its directory; the scripts refuse it")
    text = p.read_text()
    if f"kind='{kind}'" not in text:
        return Finding("fail", where, f"{CONF_DIR}/{acct}.conf says another kind than {kind}")
    wanted = rendered.get(f"bots/{acct}.conf")
    if wanted is not None and text != wanted:
        return Finding("warn", where, f"{CONF_DIR}/{acct}.conf differs from the render of this file (fleet apply conf)")
    return Finding("ok", where, f"{CONF_DIR}/{acct}.conf is root's and current")


def check_lists(host, m, rendered, machine):
    if m.get("family") == "nixos":
        return Finding("skip", "lists", "NixOS takes the rendered lists through its module, no /etc/fleet/lists.sh")
    p = host.path(LISTS)
    if not p.is_file():
        return Finding("fail", "lists", f"{LISTS} is missing: install-accounts.sh refuses to run (fleet apply lists, as root)")
    if not (mode_ok(host, p) and mode_ok(host, p.parent)):
        return Finding("fail", "lists", f"{LISTS} must be root's and not writable by others; install-accounts.sh refuses it")
    wanted = rendered.get(f"machines/{machine}/lists.sh")
    if wanted is not None and p.read_text() != wanted:
        return Finding("warn", "lists", f"{LISTS} differs from the render of this file (fleet apply lists)")
    return Finding("ok", "lists", f"{LISTS} is root's and current")


def check_scripts(host, m):
    if m.get("family") == "nixos":
        return [Finding("skip", "scripts", "NixOS places bot-round, bot-review and the units through its module")]
    out = []
    for s in SCRIPTS:
        p = host.path(s)
        if not p.is_file():
            out.append(Finding("fail", "scripts", f"{s} is missing (install-accounts.sh rounds)"))
        elif not mode_ok(host, p) or not host.mode(p) & 0o111:
            out.append(Finding("fail", "scripts", f"{s} must be root's, executable and not writable by others"))
    missing = [u for u in UNITS if not host.path(u).is_file()]
    if missing:
        out.append(Finding("fail", "scripts", "units missing: " + ", ".join(Path(u).name for u in missing) + " (install-accounts.sh rounds)"))
    if not host.path(DEFAULTS_FILE).is_file():
        out.append(Finding("warn", "scripts", f"{DEFAULTS_FILE} is missing (BOT_ROUND_MAX; install-accounts.sh rounds writes it)"))
    if not out:
        out.append(Finding("ok", "scripts", "bot-round, bot-review, fleet-token and the four units are installed, root's"))
    return out


def check_claude(host, home, where):
    p = host.path(home) / ".local" / "bin" / "claude"
    try:
        if p.exists():
            return Finding("ok", where, "claude is installed for the account")
        return Finding("fail", where, f"{home}/.local/bin/claude is missing: the unit's condition fails, no round runs")
    except PermissionError:
        return Finding("skip", where, "claude: the home is not readable (run as root)")


def check_token(host, home, where):
    d = host.path(home) / ".config" / "fleet"
    try:
        if not (d / "app-id").is_file() or not (d / "app.pem").is_file():
            return Finding("fail", where, f"{home}/.config/fleet/app-id or app.pem is missing: fleet-token has no App "
                                          "(install-accounts.sh app <bot>)")
        if host.mode(d / "app.pem") & 0o077:
            return Finding("warn", where, f"{home}/.config/fleet/app.pem is readable by others; 600")
        return Finding("ok", where, "the App's id and key are in place")
    except PermissionError:
        return Finding("skip", where, "App key: the home is not readable (run as root)")


def check_timer(host, unit, paused, where):
    st = host.unit(unit)
    enabled = st.get("UnitFileState") == "enabled"
    active = st.get("ActiveState") == "active"
    if paused:
        if active:
            return Finding("warn", where, f"{unit} runs, but the project is paused in the file (systemctl disable --now)")
        return Finding("ok", where, f"{unit} is off, the project is paused")
    if not enabled or not active:
        return Finding("fail", where, f"{unit} is {st.get('UnitFileState') or 'unknown'}/{st.get('ActiveState') or 'unknown'}: "
                                      "no rounds (install-accounts.sh rounds <bot>, systemctl start)")
    return Finding("ok", where, f"{unit} is enabled and active")


def check_status(host, agents_dir, name, kind, every_s, paused, where):
    suffix = "round" if kind == "dev" else "review"
    p = host.path(f"{agents_dir}/status/{name}-{suffix}.json")
    if paused:
        return Finding("ok", where, "paused: the status file is not expected to move")
    if not p.is_file():
        return Finding("warn", where, f"no status/{name}-{suffix}.json yet: no round has run (systemctl start the service once)")
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        return Finding("warn", where, f"status/{name}-{suffix}.json is not readable JSON: {e}")
    if d.get("signed_in") is False:
        return Finding("fail", where, f"Claude is not signed in for {name}: every round fails (bot {name}, then /login)")
    age = host.age(p)
    if age > 2 * every_s + ROUND_TIMEOUT_S:
        return Finding("warn", where, f"last round {int(age // 3600)} h ago, the timer says every {every_s // 60} min")
    if d.get("outcome") in ("offline", "retry"):
        return Finding("warn", where, f"last round ended {d.get('outcome')}: {d.get('what', '')}".rstrip(": "))
    return Finding("ok", where, f"last round {int(age // 60)} min ago, {d.get('outcome', '?')}")


def check_inbox(host, agents_dir, name, account, guards, where):
    p = host.path(f"{agents_dir}/inbox/{name}")
    if not p.is_dir():
        return Finding("fail", where, f"{agents_dir}/inbox/{name} is missing: nobody can write to {name} (install-accounts.sh inbox)")
    owner = host.owner(p)
    notes = []
    if owner not in (name, account) and owner not in guards:
        notes.append(f"owned by {owner}, not the recipient ({account}) or a guard")
    if not (p / "done").is_dir():
        notes.append("no done/ directory")
    try:
        waiting = [f for f in p.iterdir() if f.is_file() and not f.name.startswith(".")]
    except PermissionError:
        # 1730: a member of the group may drop a note, not list the inbox; root and the recipient see the count
        return Finding("skip", where, f"{agents_dir}/inbox/{name}: in place, not listable by this caller (1730; root sees the count)"
                       + ("; " + "; ".join(notes) if notes else ""))
    oldest = max((host.age(f) for f in waiting), default=0)
    if oldest > INBOX_OLD_S:
        notes.append(f"{len(waiting)} note(s) waiting, the oldest {int(oldest // 86400)} days")
    if notes:
        return Finding("warn", where, "; ".join(notes))
    return Finding("ok", where, f"{len(waiting)} note(s) waiting" if waiting else "empty")


def check_agents(host, agents_dir, machine, guard):
    out = []
    a = host.path(agents_dir)
    if not a.is_dir():
        return [Finding("fail", "agents", f"{agents_dir} is missing: no LOG, no inboxes, no status files")]
    log = a / "LOG.md"
    if not log.is_file():
        out.append(Finding("warn", "agents", f"{agents_dir}/LOG.md is missing"))
    elif not host.mode(log) & 0o020:
        out.append(Finding("warn", "agents", f"{agents_dir}/LOG.md is not group-writable: the bots cannot log"))
    st = a / "status"
    if not st.is_dir():
        out.append(Finding("fail", "agents", f"{agents_dir}/status is missing: the rounds cannot write their status"))
    elif not host.mode(st) & 0o020:
        out.append(Finding("warn", "agents", f"{agents_dir}/status is not group-writable"))
    rep = a / "reports" / machine
    if guard:
        latest = rep / "latest.txt"
        if not latest.is_file():
            out.append(Finding("warn", "agents", f"{agents_dir}/reports/{machine}/latest.txt is missing: {guard}'s check has not reported here"))
        elif host.age(latest) > GUARD_REPORT_OLD_S:
            out.append(Finding("warn", "agents", f"{guard}'s report is {int(host.age(latest) // 3600)} h old: its hourly check is not running"))
    if not out:
        out.append(Finding("ok", "agents", f"{agents_dir}: LOG.md, status/" + (f", {guard}'s report fresh" if guard else "")))
    return out


def check_rules(host):
    if host.path(RULES).is_file():
        return Finding("ok", "rules", f"{RULES} is installed (whether it is current is the guard's check)")
    return Finding("warn", "rules", f"{RULES} is missing: the sessions get no house rules")


def run(doc, machine=None, root="/", systemctl=None, user_of=None, owner_of=None, now=None):
    """The machine half's findings for `machine` (default: this host)."""
    machine = machine or os.uname().nodename
    host = Host(root, systemctl, user_of, owner_of, now)
    m, out = check_machine(doc, machine)
    if m is None:
        return out
    fl = render.Fleet(doc)
    rendered = render.render(doc)
    agents_dir = fl.fleet.get("agents_dir", "/srv/agents")
    defaults = doc.get("defaults") or {}
    every = {"dev": seconds((defaults.get("round") or {}).get("every")),
             "plan": seconds((defaults.get("review") or {}).get("every"))}
    guards = set(doc.get("guards") or {})
    mine = [(pname, p, kind, b, acct) for pname, p, kind, b, acct, bm in fl.bots() if bm == machine]
    ssh_guards = [g for g, gv in (doc.get("guards") or {}).items()
                  if ((doc["machines"].get(gv.get("machine")) or {}).get("courier") or {}).get("to") == machine]
    for pname, p, kind, b, acct in mine:
        paused = bool(p.get("paused"))
        where = f"{acct}"
        acc = check_account(host, fl, acct, machine, f"account:{where}")
        out += acc
        home = host.user(acct)
        if home is None:
            continue
        if kind == "dev":
            out.append(check_scratch(host, fl, acct, machine, f"scratch:{where}"))
        out.append(check_conf(host, rendered, acct, kind, f"conf:{where}"))
        out.append(check_claude(host, home, f"claude:{where}"))
        out.append(check_token(host, home, f"token:{where}"))
        unit = f"bot-round@{acct}.timer" if kind == "dev" else f"bot-review@{acct}.timer"
        out.append(check_timer(host, unit, paused, f"timer:{where}"))
        out.append(check_status(host, agents_dir, b["name"], kind, every[kind], paused, f"status:{b['name']}"))
    for g in ssh_guards:
        out += check_account(host, fl, g, machine, f"account:{g}")
    out.append(check_lists(host, m, rendered, machine))
    out += check_scripts(host, m)
    # every bot and guard of the fleet has an inbox here: the courier carries the notes across
    accounts = {b["name"]: a for *_, b, a, _m in fl.bots()}
    for name in list(accounts) + sorted(guards):
        out.append(check_inbox(host, agents_dir, name, accounts.get(name, name), guards, f"inbox:{name}"))
    out += check_agents(host, agents_dir, machine, m.get("guard"))
    out.append(check_rules(host))
    return out
