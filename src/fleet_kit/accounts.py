"""`fleet apply accounts` and `fleet apply inbox`: the Unix side of a bots machine (arch family).

accounts: the agents directory and its group; for every bot of this machine and every guard
whose courier delivers here, a Unix account (`useradd`, home `home_root/<account>` 750, in
the agents group, linger on, `~/agents` a link to the agents directory); for a dev bot its
scratch directory `scratch_root/<account>` and podman's `storage.conf` pointing its images
there; the machine's `admin` (an optional `[machines.<m>] admin = "<login>"`) in every bot's
group, so it reads the homes. ssh keys, sudoers, deploy wrappers and anything else the machine
needs stay with the machine's own install script.

inbox: `agents_dir/inbox/<name>/` for every bot and guard of the fleet (the register names;
the courier carries notes to the other machine), owned by its recipient when that lives here,
else by the courier guard that delivers for it; mode 1730 (the agents group may drop a note,
not list), `done/` (and `sent/` for a courier guard) 700, and the ACLs haku's fleet uses: the
admin reads everything, a default entry keeps the recipient's and the admin's rights on what
the group drops in. What is in an inbox already is chowned to its recipient.

Everything that changes the system goes through `run(argv)`, so the tests record the calls;
files and directories go under `root`. Idempotent; `--dry-run` reports and does nothing."""

import grp
import os
import pwd
import stat
import subprocess
from pathlib import Path

from . import render

GROUP = "agents"


class System:
    def __init__(self, root="/", run=None, dry_run=False, user_exists=None, group_exists=None):
        self.root = Path(root)
        self._run = run
        self.dry_run = dry_run
        self._user_exists = user_exists
        self._group_exists = group_exists
        self.is_root = os.geteuid() == 0
        self.calls = []

    def path(self, p):
        return self.root / str(p).lstrip("/")

    def run(self, *argv):
        """A system command; recorded, skipped in a dry run or when not root (a trial)."""
        self.calls.append(list(argv))
        if self.dry_run or not self.is_root or self.root != Path("/"):
            if self._run is None:
                return ""
        if self._run is not None:
            return self._run(list(argv))
        try:
            return subprocess.run(list(argv), capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def user(self, name):
        if self._user_exists is not None:
            return self._user_exists(name)
        try:
            pwd.getpwnam(name)
            return True
        except KeyError:
            return False

    def group(self, name):
        if self._group_exists is not None:
            return self._group_exists(name)
        try:
            grp.getgrnam(name)
            return True
        except KeyError:
            return False

    def chown(self, p, user, group=None):
        self.calls.append(["chown", "-h", f"{user}:{group or user}", str(p)])
        if self.dry_run or not self.is_root:
            return
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid if group else pwd.getpwnam(user).pw_gid
        os.lchown(p, uid, gid)

    def ensure_dir(self, rel, mode, user=None, group=None):
        p = self.path(rel)
        was = p.is_dir()
        if was and stat.S_IMODE(p.stat().st_mode) == mode:
            return "kept"
        if not self.dry_run:
            p.mkdir(parents=True, exist_ok=True)
            os.chmod(p, mode)
            if user:
                self.chown(p, user, group)
        return "changed" if was else "added"

    def ensure_link(self, rel, target, user):
        p = self.path(rel)
        if p.is_symlink() and os.readlink(p) == target:
            return "kept"
        if p.exists() or p.is_symlink():
            return "skipped"   # something else is there: not ours to replace
        if not self.dry_run:
            os.symlink(target, p)
            self.chown(p, user)
        return "added"

    def ensure_file(self, rel, text, mode, user=None, group=None):
        p = self.path(rel)
        if p.is_file():
            return "kept"   # never rewritten: the account may have changed it
        if not self.dry_run:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            os.chmod(p, mode)
            if user:
                self.chown(p, user, group)
        return "added"


def machine_of(doc, machine):
    m = (doc.get("machines") or {}).get(machine)
    if not m or m.get("role") != "bots":
        return None, [("skipped", f"{machine} is not a bots machine in the file")]
    if m.get("family") == "nixos":
        return None, [("skipped", f"{machine} is NixOS: its module declares the accounts, nothing to do here")]
    return m, []


def courier_guards(doc, machine):
    """The guards of other machines whose courier delivers to this one: they get an account here."""
    return [g for g, gv in (doc.get("guards") or {}).items()
            if (((doc.get("machines") or {}).get(gv.get("machine")) or {}).get("courier") or {}).get("to") == machine]


def apply_accounts(doc, machine, root="/", dry_run=False, run=None, user_exists=None, group_exists=None):
    m, out = machine_of(doc, machine)
    if m is None:
        return out
    s = System(root, run, dry_run, user_exists, group_exists)
    fl = render.Fleet(doc)
    agents_dir = fl.fleet.get("agents_dir", "/srv/agents")
    admin = m.get("admin")
    if not s.group(GROUP):
        s.run("groupadd", GROUP)
        out.append(("added", f"group {GROUP}"))
    out.append((s.ensure_dir(agents_dir, 0o2775, "root", GROUP), f"{agents_dir} (2775, group {GROUP})"))
    for sub in ("inbox", "status"):
        out.append((s.ensure_dir(f"{agents_dir}/{sub}", 0o2775 if sub == "status" else 0o755, "root", GROUP if sub == "status" else "root"),
                    f"{agents_dir}/{sub}"))
    if admin and s.user(admin):
        s.run("usermod", "-aG", GROUP, admin)
    mine = [(kind, b, acct) for pname, p, kind, b, acct, bm in fl.bots() if bm == machine]
    accounts = [(kind, acct) for kind, b, acct in mine] + [("guard", g) for g in courier_guards(doc, machine)]
    for kind, acct in accounts:
        home, scratch = fl.paths(machine, acct)
        if not s.user(acct):
            s.run("useradd", "--create-home", "--home-dir", home, "--groups", GROUP, "--shell", "/bin/bash",
                  "--comment", f"{'guard' if kind == 'guard' else 'bot'} {acct} (fleet)", acct)
            out.append(("added", f"account {acct}, home {home}"))
        out.append((s.ensure_dir(home, 0o750, acct, acct), f"{home} (750)"))
        if admin and s.user(admin):
            s.run("usermod", "-aG", acct, admin)
        if not s.path(f"/var/lib/systemd/linger/{acct}").exists():
            s.run("loginctl", "enable-linger", acct)
            out.append(("added", f"linger for {acct}"))
        out.append((s.ensure_link(f"{home}/agents", agents_dir, acct), f"{home}/agents -> {agents_dir}"))
        if kind == "dev":
            out.append((s.ensure_dir(scratch, 0o750, acct, acct), f"{scratch} (750)"))
            out.append((s.ensure_dir(f"{home}/.config", 0o700, acct, acct), f"{home}/.config"))
            out.append((s.ensure_dir(f"{home}/.config/containers", 0o700, acct, acct), f"{home}/.config/containers"))
            out.append((s.ensure_file(f"{home}/.config/containers/storage.conf",
                                      f'[storage]\ndriver = "overlay"\ngraphroot = "{scratch}/containers"\n', 0o600, acct, acct),
                        f"{home}/.config/containers/storage.conf"))
    if not admin:
        out.append(("skipped", f"no admin for {machine} in the file ([machines.{machine}] admin = \"<login>\"): "
                               "nobody but root reads the bots' homes"))
    if s.root != Path("/") or (not s.is_root and not dry_run):
        out.append(("skipped", "trial: groupadd, useradd, usermod and loginctl recorded, not run"))
    return out


def inbox_owner(doc, fl, name, machine, here_accounts):
    """Who owns an inbox on this machine: the recipient when it lives here, else the courier
    guard of the recipient's machine, else nobody (left to root)."""
    machines = doc.get("machines") or {}
    for pname, p, kind, b, acct, bm in fl.bots():
        if b["name"] == name:
            if bm == machine:
                return acct
            g = (machines.get(bm) or {}).get("guard")
            return g if g in here_accounts else None
    g = (doc.get("guards") or {}).get(name)
    if g:
        if g.get("machine") == machine:
            return name
        return name if name in here_accounts else None
    return None


def apply_inbox(doc, machine, root="/", dry_run=False, run=None, user_exists=None, group_exists=None):
    m, out = machine_of(doc, machine)
    if m is None:
        return out
    s = System(root, run, dry_run, user_exists, group_exists)
    fl = render.Fleet(doc)
    agents_dir = fl.fleet.get("agents_dir", "/srv/agents")
    admin = m.get("admin")
    inbox = f"{agents_dir}/inbox"
    out.append((s.ensure_dir(inbox, 0o755, "root", "root"), inbox))
    couriers = courier_guards(doc, machine)
    here = {acct for *_, acct, bm in fl.bots() if bm == machine} | set(couriers) | {g for g, gv in (doc.get("guards") or {}).items() if gv.get("machine") == machine}
    names = [b["name"] for *_, b, _a, _m in fl.bots()] + sorted(doc.get("guards") or {})
    for name in names:
        owner = inbox_owner(doc, fl, name, machine, here)
        if owner is None or not s.user(owner):
            out.append(("skipped", f"{inbox}/{name}: no account here to own it" + (f" ({owner} missing: fleet apply accounts)" if owner else "")))
            continue
        d = f"{inbox}/{name}"
        p = s.path(d)
        fresh = not p.is_dir()
        out.append((s.ensure_dir(d, 0o1730, owner, GROUP), f"{d} (1730, {owner}:{GROUP})"))
        if not fresh:
            # what is there already (notes, done/): the recipient's, readable by the admin
            s.run("find", d, "-mindepth", "1", "-exec", "chown", "-h", f"{owner}:{owner}", "{}", "+")
            s.run("find", d, "-mindepth", "1", "-exec", "setfacl", "-b", "{}", "+", "-exec", "chmod", "u=rwX,go=", "{}", "+")
            if admin:
                s.run("find", d, "-mindepth", "1", "-exec", "setfacl", "-m", f"u:{admin}:rwX", "{}", "+")
                s.run("find", d, "-mindepth", "1", "-type", "d", "-exec", "setfacl", "-d", "-m", f"u:{admin}:rwX", "{}", "+")
        subs = ("done", "sent") if name in couriers else ("done",)   # a courier keeps carried mail in sent/
        for sub in subs:
            out.append((s.ensure_dir(f"{d}/{sub}", 0o700, owner, owner), f"{d}/{sub}"))
        s.run("setfacl", "-b", d)
        if admin:
            s.run("setfacl", "-m", f"u:{admin}:rwx", d)
        # rwx, not rw: a default entry without x leaves a directory the recipient creates without
        # its execute bit (fleet-post's sent/, 2026-10-12)
        s.run("setfacl", "-d", "-m", f"u::rwx,u:{owner}:rwx" + (f",u:{admin}:rwx" if admin else "") + ",g::-,o::-", d)
    if not admin:
        out.append(("skipped", f"no admin for {machine} in the file: the inboxes get no ACL for a person, root reads them"))
    if s.root != Path("/") or (not s.is_root and not dry_run):
        out.append(("skipped", "trial: chown, setfacl recorded, not run"))
    return out
