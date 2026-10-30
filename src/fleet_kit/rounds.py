"""`fleet apply rounds`: the headless rounds on a bots machine of the arch family.

Installs, root-owned, what the kit ships in `fleet_kit/data`: `bot-round`, `bot-review` and
`fleet-token` to /usr/local/bin (0755), the four template units to /etc/systemd/system (0644,
the machine's `home_root` written in), makes `agents_dir/status` (the status files, setgid,
group of `agents_dir`) and each bot's `work/` or `review/` and `log/` (750, the bot's), then
`systemctl daemon-reload`, enables the timers of the bots of this machine and disables the
timers of a paused project. It starts nothing: the first round is the owner's `systemctl start`
(the pull request says why), or `--start` here. Idempotent, `--dry-run` says what would change.
A NixOS machine gets nothing from this: its module takes the rendered files.

Returns (action, what) pairs like render.apply_conf: added, changed, kept, removed,
enabled, disabled, started, skipped, missing."""

import grp
import importlib.resources
import os
import pwd
import stat
import subprocess
import tempfile
from pathlib import Path

from . import render

SCRIPTS = ("bot-round", "bot-review", "fleet-token")
UNITS = ("bot-round@.service", "bot-round@.timer", "bot-review@.service", "bot-review@.timer")
BIN_DIR = "/usr/local/bin"
UNIT_DIR = "/etc/systemd/system"


def data(name):
    return (importlib.resources.files("fleet_kit") / "data" / name).read_text()


class Target:
    """Where to write: a root to prefix, and systemctl, which the tests fake."""

    def __init__(self, root="/", systemctl=None, dry_run=False):
        self.root = Path(root)
        self._systemctl = systemctl
        self.dry_run = dry_run
        self.is_root = os.geteuid() == 0
        self.trial = False   # set when a trial root kept systemctl from running

    def path(self, p):
        return self.root / str(p).lstrip("/")

    def systemctl(self, *args):
        """`show` is read-only and runs in a dry run too; daemon-reload, enable, disable and
        start do not. A trial root (--dir) never talks to the real systemd: without a fake it
        sees no units and changes none (the summary says so)."""
        if self.dry_run and args[0] != "show":
            return ""
        if self._systemctl is not None:
            return self._systemctl(list(args))
        if self.root != Path("/"):
            self.trial = True
            return ""
        try:
            return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def unit_state(self, unit):
        out = self.systemctl("show", unit, "-p", "UnitFileState", "-p", "ActiveState", "--no-pager")
        d = {}
        for line in out.splitlines():
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
        return d

    def chown(self, p, user=None, group=None):
        if self.dry_run or not self.is_root:
            return
        uid = pwd.getpwnam(user).pw_uid if user else -1
        gid = grp.getgrnam(group).gr_gid if group else -1
        os.chown(p, uid, gid)

    def install_text(self, rel, text, mode):
        """A root-owned file with that content and mode; 'added', 'changed' or 'kept'."""
        p = self.path(rel)
        old = p.read_text() if p.is_file() else None
        if old == text and stat.S_IMODE(p.stat().st_mode) == mode:
            return "kept"
        if not self.dry_run:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".fleet-")
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.chmod(tmp, mode)
            self.chown(tmp, "root", "root")
            os.replace(tmp, p)
        return "added" if old is None else "changed"

    def ensure_dir(self, rel, mode, user=None, group=None):
        p = self.path(rel)
        was = p.is_dir()
        if was and stat.S_IMODE(p.stat().st_mode) == mode:
            return "kept"
        if not self.dry_run:
            p.mkdir(parents=True, exist_ok=True)
            os.chmod(p, mode)
            self.chown(p, user, group)
        return "changed" if was else "added"


def group_of(t, rel, fallback="root"):
    try:
        return grp.getgrgid(t.path(rel).stat().st_gid).gr_name
    except (OSError, KeyError):
        return fallback


def apply(doc, machine, root="/", dry_run=False, systemctl=None, start=False):
    t = Target(root, systemctl, dry_run)
    m = (doc.get("machines") or {}).get(machine)
    if not m or m.get("role") != "bots":
        return [("skipped", f"{machine} is not a bots machine in the file")]
    if m.get("family") == "nixos":
        return [("skipped", f"{machine} is NixOS: its module takes the rendered files, nothing to install here")]
    fl = render.Fleet(doc)
    agents_dir = fl.fleet.get("agents_dir", "/srv/agents")
    home_root = m.get("home_root", "/srv")
    out = []
    for s in SCRIPTS:
        out.append((t.install_text(f"{BIN_DIR}/{s}", data(s), 0o755), f"{BIN_DIR}/{s}"))
    for u in UNITS:
        out.append((t.install_text(f"{UNIT_DIR}/{u}", data(u).replace("{home_root}", home_root), 0o644), f"{UNIT_DIR}/{u}"))
    out.append((t.ensure_dir(f"{agents_dir}/status", 0o2775, "root", group_of(t, agents_dir)), f"{agents_dir}/status"))
    mine = [(p, kind, b, acct) for pname, p, kind, b, acct, bm in fl.bots() if bm == machine]
    t.systemctl("daemon-reload")
    for p, kind, b, acct in mine:
        home, _ = fl.paths(machine, acct)
        try:
            pwd.getpwnam(acct)
        except KeyError:
            if t.root == Path("/"):
                out.append(("missing", f"account {acct}: no user, no directories, no timer (fleet apply accounts)"))
                continue
        for d in (("work", "log") if kind == "dev" else ("review", "log")):
            out.append((t.ensure_dir(f"{home}/{d}", 0o750, acct, acct), f"{home}/{d}"))
        unit = f"bot-round@{acct}.timer" if kind == "dev" else f"bot-review@{acct}.timer"
        st = t.unit_state(unit)
        enabled = st.get("UnitFileState") == "enabled"
        active = st.get("ActiveState") == "active"
        if p.get("paused"):
            if enabled or active:
                t.systemctl("disable", "--now", unit)
                out.append(("disabled", f"{unit} (the project is paused)"))
            else:
                out.append(("kept", f"{unit} off, the project is paused"))
            continue
        if not enabled:
            t.systemctl("enable", unit)
            out.append(("enabled", unit))
        else:
            out.append(("kept", f"{unit} enabled"))
        if start and not active:
            t.systemctl("start", unit)
            out.append(("started", unit))
        elif not active:
            out.append(("kept", f"{unit} not started: systemctl start {unit} when the first round by hand was good, or --start"))
    if t.trial:
        out.append(("skipped", f"trial root {t.root}: systemctl not called, no unit seen, enabled or started"))
    return out
