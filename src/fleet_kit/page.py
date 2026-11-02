"""`fleet apply kit` and `fleet apply page`: the kit itself and the page's collector on a bots machine.

apply kit (sudo): a root-owned copy of the running kit under KIT_DIR (the package's .py files and
data/, byte-compiled, nothing else; a file the copy has and the source no longer has goes) and
the launcher LAUNCHER (`python3 -I` with that copy on sys.path), so a system unit can run `fleet`
without anybody's clone, pip or a virtualenv: nothing root runs stays writable by an agent.
Updating is running the new clone's kit once more:
`sudo env PYTHONPATH=<clone>/src python3 -m fleet_kit apply kit`. A SOURCE file next to the copy
says where it came from (path; commit when the source is a git checkout).

apply page (sudo; a bots machine of the arch family): the fleet's file as FLEET_FILE (the
collector reads it there), the page's directory `<agents_dir>/fleet` and its `state/` (the guard's,
world-readable: the JSON and the sets' state go there), the kit's page files from data/page
(index.html, sets/*.js; root-owned), `--assets DIR` mirrored into `assets/` (identities, sprites,
own sets; root-owned), the unit and the timer from data/ with this machine's guard, home,
agents_dir and `[page] collect_every` filled in, `daemon-reload`, the timer enabled and running;
`--start` runs the collector once right away. An older collector's quest.json goes once both
state files exist (the kit's first run takes it over). Refuses until `apply kit` put this very
kit on the machine: the unit runs that copy. NixOS: nothing, as with rounds. The serve lines
(by `[machines.<m>] page = { serve = "tailscale" | "caddy" | "nginx" | "none", path = "/fleet" }`)
are printed, never run: the proxy is the owner's.

Returns (action, what) pairs: added, changed, kept, removed, enabled, started, skipped, missing."""

import compileall
import importlib.resources
import os
import py_compile
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import render
from .rounds import BIN_DIR, UNIT_DIR, Target, data, group_of

KIT_DIR = "/usr/local/lib/fleet/kit"
LAUNCHER = f"{BIN_DIR}/fleet"
FLEET_FILE = "/etc/fleet/fleet.toml"
UNITS = ("fleet-collect.service", "fleet-collect.timer")
TIMER = "fleet-collect.timer"
SERVICE = "fleet-collect.service"
SERVE = ("tailscale", "caddy", "nginx", "none")


def package_dir():
    return Path(str(importlib.resources.files("fleet_kit")))


def kit_files(src):
    """{relative path: bytes} of what the copy holds: the package's .py files and data/, no caches."""
    src = Path(src)
    out = {}
    for p in sorted(src.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo"):
            continue
        rel = p.relative_to(src)
        if rel.parts[0] == "data" or p.suffix == ".py":
            out[str(rel)] = p.read_bytes()
    return out


def source_note(src, run=None):
    """Where the copy came from: the path and, for a git checkout, its commit."""
    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout)
    commit = "-"
    git = ["git", "-c", f"safe.directory={src}", "-C", str(src)]   # root reading another user's clone
    try:
        head = run([*git, "rev-parse", "HEAD"]).strip()
        if re.fullmatch(r"[0-9a-f]{40}", head):
            commit = head
            if run([*git, "status", "--porcelain", "--", "."]).strip():
                commit += " (with local changes)"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return f"source={src}\ncommit={commit}\n"


def launcher(python=None):
    py = python or ("/usr/bin/python3" if Path("/usr/bin/python3").is_file() else sys.executable)
    return (f"#!{py} -I\n"
            f"# fleet: fleet-kit as `fleet apply kit` installed it, a root-owned copy under {KIT_DIR}\n"
            "# (SOURCE there says from where). -I: no user site, no PYTHON* variables, not the current directory.\n"
            "import sys\n"
            f"sys.path.insert(0, \"{KIT_DIR}\")\n"
            "from fleet_kit.cli import main\n"
            "sys.exit(main())\n")


class Site(Target):
    """rounds.Target with files of any content and removal."""

    def install_bytes(self, rel, content, mode):
        p = self.path(rel)
        old = p.read_bytes() if p.is_file() else None
        if old == content and stat.S_IMODE(p.stat().st_mode) == mode:
            return "kept"
        if not self.dry_run:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".fleet-")
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.chmod(tmp, mode)
            self.chown(tmp, "root", "root")
            os.replace(tmp, p)
        return "added" if old is None else "changed"

    def remove(self, rel):
        p = self.path(rel)
        if not p.is_file():
            return "kept"
        if not self.dry_run:
            p.unlink()
        return "removed"


def kit_matches(t, src=None):
    """True when the copy under KIT_DIR is this kit (file for file) and the launcher is there."""
    base = t.path(f"{KIT_DIR}/fleet_kit")
    return base.is_dir() and t.path(LAUNCHER).is_file() and kit_files(base) == kit_files(src or package_dir())


def apply_kit(root="/", dry_run=False, src=None, python=None, run=None):
    t = Site(root, None, dry_run)
    src = Path(src or package_dir())
    base = f"{KIT_DIR}/fleet_kit"
    wanted = kit_files(src)
    out = [(t.ensure_dir(KIT_DIR, 0o755, "root", "root"), KIT_DIR)]
    for rel, content in wanted.items():
        out.append((t.install_bytes(f"{base}/{rel}", content, 0o644), f"{base}/{rel}"))
    have = kit_files(t.path(base)) if t.path(base).is_dir() else {}
    for rel in sorted(set(have) - set(wanted)):
        out.append((t.remove(f"{base}/{rel}"), f"{base}/{rel} (the kit no longer has it)"))
    out.append((t.install_text(f"{KIT_DIR}/SOURCE", source_note(src, run), 0o644), f"{KIT_DIR}/SOURCE"))
    out.append((t.install_text(LAUNCHER, launcher(python), 0o755), LAUNCHER))
    if not dry_run:
        # byte-compile the copy: the unit's python cannot write caches under a read-only /usr, and
        # a checked hash keeps a pyc honest whatever the mtimes say
        for d in sorted(t.path(base).rglob("__pycache__"), reverse=True):
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        if not compileall.compile_dir(str(t.path(base)), quiet=2, force=True,
                                      invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH):
            out.append(("missing", f"{base}: byte-compiling failed (python3 -m compileall {t.path(base)} says why)"))
    return out


def unit_text(name, guard, home, agents_dir, every, gpu):
    gpu_lines = ("# No PrivateDevices and no ProtectProc: nvidia-smi needs /dev/nvidia* and /proc for the GPU line."
                 if gpu else
                 "# No GPU on this machine ([machines.<m>] gpu = false): no devices and no other processes.\n"
                 "PrivateDevices=yes\nProtectProc=invisible")
    return (data(name).replace("{guard}", guard).replace("{home}", home).replace("{agents_dir}", agents_dir)
            .replace("{every}", every).replace("{gpu}", gpu_lines))


def copy_tree(t, src, dst):
    """src's files under dst, root-owned 644, every directory of src under dst 755 root, set
    explicitly: a directory the page's parent passes a default ACL to (an agents directory
    usually has one, so dropped notes stay readable) would otherwise come up group-writable
    and let any agent of the group swap a script the owner's browser loads."""
    out = []
    for p in sorted(Path(src).rglob("*")):
        rel = p.relative_to(src)
        if p.is_dir():
            out.append((t.ensure_dir(f"{dst}/{rel}", 0o755, "root", "root"), f"{dst}/{rel}"))
        elif p.is_file():
            out.append((t.install_bytes(f"{dst}/{rel}", p.read_bytes(), 0o644), f"{dst}/{rel}"))
    return out


def mirror(t, src, dst):
    """src's files under dst, root-owned 644; a file under dst that src does not have goes."""
    out = []
    src = Path(src)
    if not src.is_dir():
        return [("missing", f"--assets {src}: not a directory")]
    out.append((t.ensure_dir(dst, 0o755, "root", "root"), dst))
    for p in sorted(src.rglob("*")):   # every directory set explicitly: see copy_tree
        if p.is_dir() and "__pycache__" not in p.parts:
            out.append((t.ensure_dir(f"{dst}/{p.relative_to(src)}", 0o755, "root", "root"), f"{dst}/{p.relative_to(src)}"))
    wanted = {str(p.relative_to(src)): p for p in sorted(src.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    for rel, p in wanted.items():
        out.append((t.install_bytes(f"{dst}/{rel}", p.read_bytes(), 0o644), f"{dst}/{rel}"))
    d = t.path(dst)
    if d.is_dir():
        for p in sorted(d.rglob("*")):
            rel = str(p.relative_to(d))
            if p.is_file() and rel not in wanted and "__pycache__" not in p.parts:
                out.append((t.remove(f"{dst}/{rel}"), f"{dst}/{rel} (not in --assets any more)"))
    return out


def guard_of(fl, machine):
    m = fl.machines.get(machine) or {}
    return m.get("guard") or next((g for g, gv in fl.guards.items() if gv.get("machine") == machine), None)


def apply_page(doc, machine, fleet_text, root="/", dry_run=False, systemctl=None, assets=None, start=False, page_data=None):
    t = Site(root, systemctl, dry_run)
    m = (doc.get("machines") or {}).get(machine)
    if not m or m.get("role") != "bots":
        return [("skipped", f"{machine} is not a bots machine in the file")]
    if m.get("family") == "nixos":
        return [("skipped", f"{machine} is NixOS: its module takes the rendered files, nothing to install here")]
    if not kit_matches(t):
        return [("missing", f"the kit under {KIT_DIR} is not this one, or not there: fleet apply kit first (the unit runs that copy)")]
    fl = render.Fleet(doc)
    guard = guard_of(fl, machine)
    if not guard:
        return [("missing", f"{machine} has no guard in the file ([machines.{machine}] guard = \"...\"): the collector runs as the guard")]
    agents_dir = fl.fleet.get("agents_dir", "/srv/agents")
    home, _ = fl.paths(machine, guard)
    every = (doc.get("page") or {}).get("collect_every") or "5m"
    fleet_dir = f"{agents_dir}/fleet"
    group = group_of(t, agents_dir)
    out = [(t.install_text(FLEET_FILE, fleet_text, 0o644), FLEET_FILE)]
    for d in (fleet_dir, f"{fleet_dir}/state"):
        out.append((t.ensure_dir(d, 0o2755, guard, group), d))
    pd = Path(page_data) if page_data else package_dir() / "data" / "page"
    if pd.is_dir():
        out += copy_tree(t, pd, fleet_dir)
    else:
        out.append(("skipped", "the kit has no page files yet (data/page): the collector runs, the page comes with the kit's next version"))
    if assets:
        out += mirror(t, assets, f"{fleet_dir}/assets")
    if t.path(f"{fleet_dir}/quest.json").is_file():
        if all(t.path(f"{fleet_dir}/state/{s}.json").is_file() for s in ("items", "fantasy")):
            out.append((t.remove(f"{fleet_dir}/quest.json"), f"{fleet_dir}/quest.json (taken over into state/)"))
        else:
            out.append(("kept", f"{fleet_dir}/quest.json until the kit's first run has taken it over (state/items.json, state/fantasy.json)"))
    timer_changed = False
    for u in UNITS:
        act = t.install_text(f"{UNIT_DIR}/{u}", unit_text(u, guard, home, agents_dir, every, bool(m.get("gpu", False))), 0o644)
        out.append((act, f"{UNIT_DIR}/{u}"))
        timer_changed |= u == TIMER and act != "kept"
    t.systemctl("daemon-reload")
    st = t.unit_state(TIMER)
    if st.get("UnitFileState") != "enabled" or st.get("ActiveState") != "active":
        t.systemctl("enable", TIMER)
        t.systemctl("start", TIMER)
        out.append(("enabled", f"{TIMER} (running)"))
    elif timer_changed:
        t.systemctl("restart", TIMER)
        out.append(("changed", f"{TIMER} restarted (its interval changed)"))
    else:
        out.append(("kept", f"{TIMER} enabled and running"))
    if start:
        t.systemctl("start", SERVICE)
        out.append(("started", f"{SERVICE} (one run now)"))
    else:
        out.append(("kept", f"no run now: systemctl start {SERVICE}, or --start; the timer's next run comes anyway"))
    if t.trial:
        out.append(("skipped", f"trial root {t.root}: systemctl not called, no unit seen, enabled or started"))
    return out


def tailscale_mounts(status):
    """(mount, target) pairs out of `tailscale serve status`."""
    return [(mt, tg) for mt, _, tg in re.findall(r"^\|--\s+(\S+)\s+(path|proxy)\s+(\S+)", status or "", re.M)]


def serve_hints(m, agents_dir, status=None):
    """How to put the page directory behind the machine's proxy, as lines to print: by
    `[machines.<m>] page = { serve, path }`; `status` is `tailscale serve status` when there is one."""
    page = m.get("page") or {}
    serve, path = page.get("serve") or "none", "/" + (page.get("path") or "/fleet").strip("/")
    d = f"{agents_dir}/fleet"
    if serve == "tailscale":
        mounts = tailscale_mounts(status)
        have = any(mt.rstrip("/") == path and tg == d for mt, tg in mounts)
        stale = [mt for mt, tg in mounts if (mt.rstrip("/") == path and tg != d) or mt.rstrip("/").startswith(path + "/")]
        out = [f"tailscale serve (the owner's, once): {'the page directory is in place' if have else 'the page directory under ' + path}"]
        for mt in stale:
            out.append(f"  sudo tailscale serve --bg --set-path {mt.rstrip('/')} off   # an older entry under {path}")
        if not have:
            out.append(f"  sudo tailscale serve --bg --set-path {path} {d}   # the directory: index.html, fleet.json, sets/, assets/")
        out.append("  tailscale serve status")
        return out
    if serve == "caddy":
        return ["caddy (the owner's), in the site block:", f"  handle_path {path}/* {{", f"    root * {d}", "    file_server", "  }"]
    if serve == "nginx":
        return ["nginx (the owner's), in the server block:", f"  location {path}/ {{ alias {d}/; }}"]
    return ["no proxy chosen ([machines.<m>] page = { serve = \"tailscale\" | \"caddy\" | \"nginx\", path = \"/fleet\" }); a trial:",
            f"  python3 -m http.server -d {d} 8000   # then http://localhost:8000/"]
