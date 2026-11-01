"""fleet collect: one JSON of the fleet's state for the page.

Runs on the bots machine as its guard from `fleet-collect.timer` (`fleet apply page`), writes
`<agents_dir>/fleet/fleet.json`, world-readable, next to the page. Read-only: GitHub through the
guard's App (`gh`, the token from `fleet-token`), the rounds' status files in `<agents_dir>/status`,
the timers through `systemctl`, the guards' reports in `<agents_dir>/reports/<machine>/latest.txt`,
the disks and the GPU. What it cannot read as the guard (an inbox, a bot's credentials) the rounds
write into their status files themselves (`inbox`, `signed_in`). A repository under another owner
is read through a second App of the guard's (`[guards.<g>] apps`, `fleet-token --app`). A project
the App is not installed on shows an error instead of data.

The JSON has a shared part, the same for every look: `projects` (open pull requests with review
and checks, the issues the lists need), `bots` (the last round, today's cost, the timer), `guards`
(the reports), `machine`, `history` (merged, closed, rounds, cost per day over HISTORY_DAYS days
and the all-time counts), `items` (what waits on the owner: merge, decide, step in; the rules in
items() below) with `since`, when each was first seen. Then `page` (the set to open with, the
sets prepared, the labels' roles, the bots' personas, the owner) and `sets.<name>`: the block of
each set's collector half (sets.py), the fantasy game's quest and levels for one. Every value
that is one fleet's comes from fleet.toml: the owner, `agents_dir`, this machine's guard, disks
and gpu, `[page.labels]`.

History is best effort: when GitHub fails for a project, the previous JSON's entry stays within
its day, marked stale, so levels do not flicker. The projects and the bots are read in parallel
(WORKERS `gh` processes at a time) and the all-time counts, search calls with the tightest rate
limit, are asked again only when the window's sums changed or the count is older than TOTALS_MIN.
A pull request's review state falls back to the reviews themselves when GitHub's reviewDecision
is empty: GitHub fills that field only where the default branch has a rule that requires a
review; without one the plan bot's approvals would never reach the merge list.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import sets as setmod
from .render import Fleet

HISTORY_DAYS = 30
TOTALS_MIN = 60    # the all-time counts (search API) are asked again after this many minutes at the latest
WORKERS = 4        # gh processes at a time (GitHub's secondary limits frown on many more)
DONE = {"built", "fix-pushed", "reviewed"}   # a round that produced something (the page's green outcomes)
BAD = {"stuck", "fix-stuck", "offline", "no-review", "unposted", "not-signed-in"}
LABEL_ROLES = {"ready": "ready", "next": "next", "decide": "needs-haku", "live": "live"}   # [page.labels] overrides
FLEET_TOKEN = "/usr/local/bin/fleet-token"


def find_gh():
    """The real gh: not a per-user wrapper in ~/.local/bin that fixes GH_TOKEN to one App's token
    (such a wrapper would hide a second owner's repositories from a run by hand)."""
    return next((w for d in os.get_exec_path() if ".local/bin" not in d for w in [shutil.which("gh", path=d)] if w), "gh")


@dataclass
class Config:
    """What the collector takes from fleet.toml and the command line."""
    owner: str
    fleet: str
    machine: str                 # this machine's name in the file
    guard: str                   # the machine's guard: the user the collector runs as
    home: str                    # the guard's home (HOME for gh when the environment has none)
    agents_dir: Path
    out: Path                    # fleet.json; the page's directory is its parent
    disks: list
    gpu: bool
    gpu_lock: Path | None
    labels: dict                 # role -> label name
    page_set: str
    page_sets: list              # the sets the collector prepares; [] means the kit's and every own one found
    knobs: dict                  # set name -> its [page.<set>] table
    projects: list               # (repo, short name)
    bots: list                   # dicts: name, kind, project (short), machine, rounds, account, app
    owner_app: dict              # repository owner -> fleet-token --app name
    machines: list
    fleet_token: str = FLEET_TOKEN
    gh: str = field(default_factory=find_gh)

    @property
    def status(self):
        return self.agents_dir / "status"

    @property
    def reports(self):
        return self.agents_dir / "reports"

    @property
    def state_dir(self):
        return self.out.parent / "state"

    @property
    def assets(self):
        return self.out.parent / "assets"


def config(doc, machine=None, out=None, fleet_token=None):
    """The Config for `machine` (default: this host's name). Raises ValueError when the file has
    no such machine: the collector runs on a machine of the file."""
    fl = Fleet(doc)
    machine = machine or os.uname().nodename
    m = fl.machines.get(machine)
    if m is None:
        raise ValueError(f"no [machines.{machine}] in the file; --machine names the one this is")
    guard = m.get("guard") or next((g for g, gv in fl.guards.items() if gv.get("machine") == machine), None) or fl.owner
    home_root = m.get("home_root", "/srv")
    agents_dir = Path(fl.fleet.get("agents_dir") or f"{home_root}/agents")
    gpu = m.get("gpu", False)
    gpu_lock = None
    if isinstance(gpu, dict):
        gpu_lock, gpu = (Path(gpu["lock"]) if gpu.get("lock") else None), True
    page = doc.get("page") or {}
    labels = dict(LABEL_ROLES, **{k: v for k, v in (page.get("labels") or {}).items() if isinstance(v, str)})
    projects = [(p.get("repo", ""), p.get("repo", "").rsplit("/", 1)[-1]) for p in fl.projects.values()]
    short = {pn: p.get("repo", "").rsplit("/", 1)[-1] for pn, p in fl.projects.items()}
    bots, botless = [], [pn for pn, p in fl.projects.items() if not any(isinstance(p.get(k), dict) for k in ("plan", "dev"))]
    for pname, p in fl.projects.items():
        for kind in ("dev", "plan"):   # dev first, as the page's cards have it
            b = p.get(kind)
            if isinstance(b, dict) and b.get("name"):
                bots.append({"name": b["name"], "kind": kind, "project": short[pname], "machine": fl.machine_of(p, b),
                             "rounds": "round" if kind == "dev" else "review", "account": b.get("account") or b["name"],
                             "app": fl.app(b), "persona": b.get("persona")})
    owner_app = {}
    for g, gv in fl.guards.items():
        proj = short.get(gv.get("project")) or (short[botless[0]] if botless else "")
        bots.append({"name": g, "kind": "guard", "project": proj, "machine": gv.get("machine", ""), "rounds": "none",
                     "account": g, "app": f"{fl.owner}-{g}", "persona": gv.get("persona")})
        for a in gv.get("apps") or []:
            if isinstance(a, dict):
                owner_app[a["owner"]] = a["name"]
    out = Path(out) if out else agents_dir / "fleet" / "fleet.json"
    return Config(owner=fl.owner, fleet=fl.fleet.get("name", ""), machine=machine, guard=guard, home=f"{home_root}/{guard}",
                  agents_dir=agents_dir, out=out, disks=list(m.get("disks") or ["/"]), gpu=bool(gpu), gpu_lock=gpu_lock,
                  labels=labels, page_set=page.get("set") or "default", page_sets=list(page.get("sets") or []),
                  knobs={k: v for k, v in page.items() if isinstance(v, dict) and k != "labels"},
                  projects=projects, bots=bots, owner_app=owner_app, machines=list(fl.machines),
                  fleet_token=fleet_token or FLEET_TOKEN)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def minutes_since(iso):
    """Minutes since an ISO timestamp of ours; None when there is none or it is unreadable."""
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except (ValueError, TypeError):
        return None


def local_day(iso):
    """GitHub's UTC timestamp as the local date, the way the logs count days (None if unreadable)."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (ValueError, AttributeError, TypeError):
        return None


def history_days():
    """The window's dates, oldest first, today last (local dates)."""
    t = date.today().toordinal()
    return [date.fromordinal(t - i).isoformat() for i in range(HISTORY_DAYS - 1, -1, -1)]


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, data, mode=None):
    """Atomically, next to the file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    if mode is not None:
        tmp.chmod(mode)
    tmp.replace(path)


class Collector:
    """One run. `run` is the one function that executes a command, (stdout, error); the tests
    hand in a fake."""

    def __init__(self, cfg, run=None):
        self.cfg = cfg
        self._run = run or self.subprocess_run
        self.calls = {"gh": 0, "search": 0}
        self._lock = threading.Lock()

    @staticmethod
    def subprocess_run(cmd, timeout=60, env=None):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, str(e)
        if p.returncode:
            return None, (p.stderr or p.stdout).strip()[:300]
        return p.stdout, None

    def run(self, *cmd, timeout=60, env=None):
        if cmd[0] == self.cfg.gh:
            with self._lock:
                self.calls["gh"] += 1
                self.calls["search"] += "search/issues" in cmd
        return self._run(list(cmd), timeout=timeout, env=env)

    def gh(self, *args, env, timeout=60):
        return self.run(self.cfg.gh, *args, env=env, timeout=timeout)

    # ---- GitHub ---------------------------------------------------------------------------
    def gh_env(self, owner):
        """gh's environment for <owner>'s repositories: the token of the App that reads them (None
        and the reason when there is none). No update check, no prompt, no colour: several gh run
        at once and none of them may touch gh's state file or wait for a terminal."""
        app = self.cfg.owner_app.get(owner)
        token, err = self.run(self.cfg.fleet_token, *(["--app", app] if app else []))
        if err or not token:
            return None, err or "fleet-token printed nothing"
        env = dict(os.environ, GH_TOKEN=token.strip(), GH_NO_UPDATE_NOTIFIER="1", GH_PROMPT_DISABLED="1", NO_COLOR="1")
        env.setdefault("HOME", self.cfg.home)
        return env, None

    def who(self, author):
        """app/<owner>-<bot> -> <bot>; a person's login stays."""
        a = author or ""
        if a.startswith("app/"):
            a = a[4:]
            a = a.removeprefix(f"{self.cfg.owner}-")
        return a

    def checks_state(self, repo, sha, env):
        """green, red, pending or none from the head commit's check runs (REST: Checks: read is
        enough; gh's statusCheckRollup also asks for the workflow run, which needs Actions: read)."""
        o, err = self.gh("api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100",
                         "--jq", "[.check_runs[] | (.conclusion // .status)]", env=env)
        if err or not o:
            return "unknown"
        runs = json.loads(o)
        if not runs:
            return "none"
        states = {(s or "").upper() for s in runs}
        if states & {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
            return "red"
        if states & {"PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED", "WAITING", ""}:
            return "pending"
        return "green"

    @staticmethod
    def review_state(pr):
        """approved, changes_requested, review_required or none: GitHub's reviewDecision when it is
        set, else the latest APPROVED or CHANGES_REQUESTED review of each reviewer but the author,
        the way the decision would count: a change request outweighs an approval. Comments, pending
        and dismissed reviews do not count."""
        decision = (pr.get("reviewDecision") or "").lower()
        if decision:
            return decision
        author = ((pr.get("author") or {}).get("login") or "").removeprefix("app/")
        latest = {}
        for r in sorted(pr.get("reviews") or [], key=lambda r: r.get("submittedAt") or ""):
            who = ((r.get("author") or {}).get("login") or "").removeprefix("app/")
            state = (r.get("state") or "").upper()
            if who and who != author and state in ("APPROVED", "CHANGES_REQUESTED"):
                latest[who] = state
        states = set(latest.values())
        return "changes_requested" if "CHANGES_REQUESTED" in states else "approved" if "APPROVED" in states else "none"

    def project(self, repo, name, env, env_err=None):
        out = {"repo": repo, "name": name, "prs": [], "issues": []}
        if env is None:
            out["error"] = f"no token: {env_err}"
            return out
        o, err = self.gh("pr", "list", "-R", repo, "--state", "open", "--limit", "50", "--json",
                         "number,title,author,headRefName,headRefOid,isDraft,reviewDecision,reviews,updatedAt,labels",
                         env=env, timeout=90)
        if err:
            out["error"] = err
            return out
        for pr in json.loads(o or "[]"):
            out["prs"].append({
                "number": pr["number"], "title": pr["title"],
                "author": (pr.get("author") or {}).get("login", ""),
                "branch": pr.get("headRefName", ""), "draft": pr.get("isDraft", False),
                "review": self.review_state(pr),
                "checks": self.checks_state(repo, pr.get("headRefOid", ""), env),
                "labels": [l["name"] for l in pr.get("labels", [])],
                "updated": pr.get("updatedAt", ""),
            })
        o, err = self.gh("issue", "list", "-R", repo, "--state", "open", "--limit", "100", "--json",
                         "number,title,labels,updatedAt", env=env, timeout=90)
        if err:
            out["error"] = err
            return out
        L = self.cfg.labels
        for i in json.loads(o or "[]"):
            labels = [l["name"] for l in i.get("labels", [])]
            if {L["ready"], L["decide"], L["next"]} & set(labels):
                out["issues"].append({"number": i["number"], "title": i["title"], "labels": labels,
                                      "updated": i.get("updatedAt", "")})
        out["issues"].sort(key=lambda i: (L["decide"] not in i["labels"], L["next"] not in i["labels"], i["number"]))
        return out

    # ---- the machine ----------------------------------------------------------------------
    def timer(self, unit):
        o, err = self.run("systemctl", "show", unit, "-p", "ActiveState,LoadState")
        if err or not o:
            return None
        kv = dict(line.split("=", 1) for line in o.splitlines() if "=" in line)
        if kv.get("LoadState") != "loaded":
            return None
        out = {"active": kv.get("ActiveState") == "active", "next": None, "last": None}
        # next and last run as ISO UTC. list-timers, not `show`: a timer with OnUnitActiveSec has no
        # NextElapseUSecRealtime in `show`
        o, _ = self.run("systemctl", "list-timers", "--all", "--output=json", unit)
        try:
            t = json.loads(o or "[]")[0]
            for k in ("next", "last"):
                if t.get(k):
                    out[k] = datetime.fromtimestamp(t[k] / 1e6, timezone.utc).isoformat(timespec="seconds")
        except (ValueError, IndexError, KeyError, TypeError, OSError):
            pass
        return out

    @staticmethod
    def cost_today(logfile):
        """Sum of the cost column of today's lines in <bot>-<kind>.log (started outcome cost turns what)."""
        today = date.today().isoformat()
        total, n = 0.0, 0
        try:
            for line in logfile.read_text(errors="replace").splitlines():
                f = line.split()
                if len(f) >= 3 and f[0].startswith(today):
                    try:
                        total += float(f[2])
                        n += 1
                    except ValueError:
                        pass
        except OSError:
            return None, 0
        return round(total, 2), n

    def bot(self, b):
        out = {k: b[k] for k in ("name", "kind", "project", "machine", "rounds")}
        if b["machine"] != self.cfg.machine:
            return out   # its state comes with its machine's guard report
        if b["rounds"] != "none":
            st = read_json(self.cfg.status / f"{b['name']}-{b['rounds']}.json")
            out["last"] = ({k: st.get(k) for k in ("started", "ended", "outcome", "what", "cost_usd", "turns", "signed_in", "inbox")}
                           if isinstance(st, dict) else None)
            out["cost_today"], out["rounds_today"] = self.cost_today(self.cfg.status / f"{b['name']}-{b['rounds']}.log")
            unit = {"round": f"bot-round@{b['account']}.timer", "review": f"bot-review@{b['account']}.timer"}[b["rounds"]]
            out["timer"] = self.timer(unit)
        return out

    def report(self, host):
        f = self.cfg.reports / host / "latest.txt"
        try:
            lines = f.read_text(errors="replace").splitlines()
            age = round((time.time() - f.stat().st_mtime) / 60)
        except OSError as e:
            return {"host": host, "error": str(e)}
        return {"host": host, "head": lines[0] if lines else "", "age_min": age,
                "findings": [l for l in lines[1:] if l[:4].strip() in ("CRIT", "WARN")][:30]}

    def machine(self):
        out = {"name": self.cfg.machine, "disks": {}, "load": None, "uptime_h": None, "gpu": {}}
        for d in self.cfg.disks:
            try:
                v = os.statvfs(d)
                out["disks"][d] = {"free_gb": round(v.f_bavail * v.f_frsize / 1e9), "size_gb": round(v.f_blocks * v.f_frsize / 1e9)}
            except OSError:
                pass
        try:
            out["load"] = [round(x, 2) for x in os.getloadavg()]
            out["uptime_h"] = round(float(Path("/proc/uptime").read_text().split()[0]) / 3600, 1)
        except OSError:
            pass
        if self.cfg.gpu:
            out["gpu"]["lock"] = None
            if self.cfg.gpu_lock:
                try:
                    out["gpu"]["lock"] = self.cfg.gpu_lock.read_text(errors="replace").strip()[:120] or None
                except OSError:
                    pass
            o, _ = self.run("nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits", timeout=15)
            if o:
                try:
                    u, m, t = [x.strip() for x in o.strip().split(",")]
                    out["gpu"].update({"util_pct": int(u), "mem_used_mb": int(m), "mem_total_mb": int(t)})
                except ValueError:
                    pass
        return out

    # ---- history --------------------------------------------------------------------------
    def history_project(self, repo, env, days, prev=None):
        """Merged pull requests (all and by author) and closed issues per day of the window, and the
        all-time counts. Two list calls per run; the two searches for the all-time counts only when
        the window's sums differ from the previous run's or the previous counts are older than
        TOTALS_MIN. (None, error) when a call fails."""
        n, idx = len(days), {d: i for i, d in enumerate(days)}
        out = {"merged": [0] * n, "merged_by": {}, "closed": [0] * n, "merged_total": None, "closed_total": None, "totals_at": None}
        o, err = self.gh("pr", "list", "-R", repo, "--state", "merged", "--limit", "500", "--search", f"merged:>={days[0]}",
                         "--json", "mergedAt,author", env=env, timeout=90)
        if err:
            return None, err
        for pr in json.loads(o or "[]"):
            i = idx.get(local_day(pr.get("mergedAt")))
            if i is None:
                continue
            out["merged"][i] += 1
            out["merged_by"].setdefault((pr.get("author") or {}).get("login", ""), [0] * n)[i] += 1
        o, err = self.gh("issue", "list", "-R", repo, "--state", "closed", "--limit", "500", "--search", f"closed:>={days[0]}",
                         "--json", "closedAt", env=env, timeout=90)
        if err:
            return None, err
        for i in json.loads(o or "[]"):
            j = idx.get(local_day(i.get("closedAt")))
            if j is not None:
                out["closed"][j] += 1
        prev = prev or {}
        age = minutes_since(prev.get("totals_at"))
        if (age is not None and age < TOTALS_MIN and prev.get("merged_total") is not None and prev.get("closed_total") is not None
                and sum(prev.get("merged") or []) == sum(out["merged"]) and sum(prev.get("closed") or []) == sum(out["closed"])):
            out.update(merged_total=prev["merged_total"], closed_total=prev["closed_total"], totals_at=prev["totals_at"])
            return out, None
        for q, k in ((f"repo:{repo} is:pr is:merged", "merged_total"), (f"repo:{repo} is:issue is:closed", "closed_total")):
            o, err = self.gh("api", "-X", "GET", "search/issues", "-f", f"q={q}", "-f", "per_page=1", "--jq", ".total_count", env=env)
            if err:
                return None, err
            out[k] = int(o.strip() or 0)
        out["totals_at"] = now_iso()
        return out, None

    def history_bot(self, name, rounds, days):
        """Rounds, cost, done and bad rounds per day of the window from the whole log, and the
        totals since its first line."""
        n, idx = len(days), {d: i for i, d in enumerate(days)}
        out = {"since": None, "rounds": [0] * n, "cost": [0.0] * n, "done": [0] * n, "bad": [0] * n,
               "rounds_total": 0, "cost_total": 0.0, "done_total": 0, "bad_total": 0}
        try:
            lines = (self.cfg.status / f"{name}-{rounds}.log").read_text(errors="replace").splitlines()
        except OSError:
            return None
        for line in lines:
            f = line.split()
            if len(f) < 3 or len(f[0]) < 10:
                continue
            day, outcome = f[0][:10], f[1]
            try:
                cost = float(f[2])
            except ValueError:
                cost = 0.0
            out["since"] = min(out["since"] or day, day)
            out["rounds_total"] += 1
            out["cost_total"] += cost
            out["done_total"] += outcome in DONE
            out["bad_total"] += outcome in BAD
            i = idx.get(day)
            if i is not None:
                out["rounds"][i] += 1
                out["cost"][i] += cost
                out["done"][i] += outcome in DONE
                out["bad"][i] += outcome in BAD
        out["cost"] = [round(c, 2) for c in out["cost"]]
        out["cost_total"] = round(out["cost_total"], 2)
        return out

    def history(self, envs, previous, pool):
        """The history block; a project's entry falls back to the previous run's (marked stale).
        The projects, then the bots, are read in parallel on the pool."""
        days = history_days()
        prev = (previous or {}).get("history") or {}
        prev_p, prev_b = prev.get("projects") or {}, prev.get("bots") or {}
        out = {"days": days, "projects": {}, "bots": {}}

        def one_project(rn):
            repo, name = rn
            env, env_err = envs[repo.split("/")[0]]
            h, err = self.history_project(repo, env, days, prev_p.get(name)) if env is not None else (None, env_err)
            if h is None:
                old = prev_p.get(name)
                h = dict(old, stale=True, error=err) if old and old.get("day") == days[-1] else {"error": err}
            h["day"] = days[-1]   # a stale copy is reused within its day only
            return name, h
        out["projects"].update(pool.map(one_project, self.cfg.projects))

        repos = {name: repo for repo, name in self.cfg.projects}
        bots = [b for b in self.cfg.bots if b["machine"] == self.cfg.machine and b["rounds"] != "none"]   # the logs are local

        def one_bot(b):
            name, proj = b["name"], b["project"]
            h = self.history_bot(name, b["rounds"], days) or {}
            # the bot's merged pull requests, all time: asked again when the project's window changed
            # or the count is older than TOTALS_MIN, else the previous count
            old, ph = prev_b.get(name) or {}, out["projects"].get(proj) or {}
            age = minutes_since(old.get("totals_at"))
            if (old.get("merged_total") is not None and age is not None and age < TOTALS_MIN
                    and sum((prev_p.get(proj) or {}).get("merged") or []) == sum(ph.get("merged") or [])):
                h["merged_total"], h["totals_at"] = old["merged_total"], old["totals_at"]
                return name, h
            env, _ = envs.get(repos.get(proj, "/").split("/")[0], (None, None))
            o, err = self.gh("api", "-X", "GET", "search/issues", "-f", f"q=repo:{repos[proj]} is:pr is:merged author:app/{b['app']}",
                             "-f", "per_page=1", "--jq", ".total_count", env=env) if env is not None else (None, "no token")
            if not err and o and o.strip().isdigit():
                h["merged_total"], h["totals_at"] = int(o.strip()), now_iso()
            else:   # the search failed: the previous count, however old
                h["merged_total"], h["totals_at"] = old.get("merged_total"), old.get("totals_at")
            return name, h
        out["bots"].update(pool.map(one_bot, bots))
        return out

    # ---- what waits on the owner ----------------------------------------------------------
    def items(self, data):
        """What only the owner can do, each with a stable id (the page's three lists). merge: a
        pull request approved by the project's reviewing plan bot with CI green or absent, or,
        where no plan bot reviews in rounds, any non-draft one that is green and not sent back.
        decide: an issue or pull request with the decide label. step: red CI on a pull request that
        is not a dev bot's (a dev bot fixes its own), a ready issue labelled live (needs the owner's
        desktop), a bot not signed in or with a bad outcome, a timer off while work waits for that
        bot, a project the collector cannot read, a guard without a report or with CRIT findings."""
        L = self.cfg.labels
        out = []

        def add(kind, tag, cls, project, id, text="", url=None, label=None, why=None, bot=None):
            out.append({"id": f"{kind}:{id}", "kind": kind, "tag": tag, "cls": cls, "project": project,
                        "text": text, "url": url, "label": label, "why": why, "bot": bot})
        bots = data["bots"]
        dev_names = {b["name"] for b in self.cfg.bots if b["kind"] == "dev"}
        dev_bot = lambda a: self.who(a) in dev_names
        for p in data["projects"]:
            name, repo = p["name"], p["repo"]
            if p.get("error"):
                add("step", "error", "red", name, f"error:{repo}", p["error"])
                continue
            rev = next((b for b in bots if b["project"] == name and b["kind"] == "plan" and b["rounds"] == "review"), None)
            pr_url = lambda n: f"https://github.com/{repo}/pull/{n}"
            issue_url = lambda n: f"https://github.com/{repo}/issues/{n}"
            ci_ok = lambda pr: pr["checks"] not in ("red", "pending")
            ci_note = lambda pr: "CI green" if pr["checks"] == "green" else "no CI here" if pr["checks"] == "none" else "CI " + pr["checks"]
            for pr in p["prs"]:
                if pr["draft"]:
                    continue
                n, t = pr["number"], pr["title"]
                if pr["review"] == "approved" and ci_ok(pr):
                    add("merge", "merge", "green", name, f"{repo}#{n}", t, pr_url(n), f"#{n}",
                        f"approved{' by ' + rev['name'] if rev else ''}, {ci_note(pr)}")
                elif not rev and pr["review"] != "changes_requested" and ci_ok(pr):
                    add("merge", "review & merge", "green", name, f"{repo}#{n}", t, pr_url(n), f"#{n}", f"by {self.who(pr['author'])}, {ci_note(pr)}")
                elif pr["checks"] == "red" and not dev_bot(pr["author"]):
                    add("step", "ci red", "red", name, f"ci:{repo}#{n}", t, pr_url(n), f"#{n}",
                        f"by {self.who(pr['author'])}; a dev bot would fix its own, this one is yours")
                if L["decide"] in pr["labels"]:
                    add("decide", "decide", "red", name, f"{repo}#{n}", t, pr_url(n), f"#{n}", f"pull request labelled {L['decide']}")
            for i in p["issues"]:
                n, t, labels = i["number"], i["title"], i["labels"]
                if L["decide"] in labels:
                    add("decide", "decide", "red", name, f"{repo}#{n}", t, issue_url(n), f"#{n}")
                elif L["live"] in labels and L["ready"] in labels:
                    add("step", "live", "amber", name, f"live:{repo}#{n}", t, issue_url(n), f"#{n}", "needs your desktop, the dev bot skips it")
            # a bot's silence only matters while work waits for it
            queued = [i for i in p["issues"] if L["ready"] in i["labels"] and L["live"] not in i["labels"]]
            unreviewed = [pr for pr in p["prs"] if dev_bot(pr["author"]) and not pr["draft"] and pr["review"] in ("none", "review_required")]
            for b in bots:
                if b["project"] != name or b["machine"] != self.cfg.machine or b["rounds"] == "none":
                    continue
                last, timer, bn = b.get("last"), b.get("timer"), b["name"]
                if last and last.get("signed_in") is False:
                    add("step", "sign in", "red", name, f"signin:{bn}", "", why="its rounds cannot run", bot=bn)
                if last and last.get("outcome") in BAD:
                    add("step", last["outcome"], "red", name, f"bad:{bn}", last.get("what") or "", bot=bn)
                if timer and not timer["active"]:
                    if b["kind"] == "dev" and queued:
                        k = len(queued)
                        add("step", "timer off", "red", name, f"timer:{bn}", "", why=f"{k} ready issue{'s' if k > 1 else ''} wait{'' if k > 1 else 's'} for it", bot=bn)
                    if b["kind"] == "plan" and unreviewed:
                        k = len(unreviewed)
                        add("step", "timer off", "red", name, f"timer:{bn}", "", why=f"{k} pull request{'s' if k > 1 else ''} wait{'' if k > 1 else 's'} for its review", bot=bn)
        for g in data["guards"]:
            if g.get("error"):
                add("step", "guard", "red", "fleet", f"guard:{g['host']}", f"{g['host']}: {g['error']}", "#machine", "report")
            else:
                crit = sum(1 for f in g["findings"] if f.startswith("CRIT"))
                if crit:
                    add("step", "CRIT", "red", "fleet", f"crit:{g['host']}", f"{g['host']}: {crit} critical finding{'s' if crit > 1 else ''}", "#machine", "report")
        return out

    def since(self, items, previous):
        """When each item was first seen, kept in state/items.json across runs; written into the
        items as `since`. An item of a project the collector cannot read right now keeps its date
        (a GitHub hiccup does not make it new again). The first run takes the dates from an older
        quest.json next to the JSON when there is one."""
        f = self.cfg.state_dir / "items.json"
        seen = read_json(f)
        if seen is None:
            seen = (read_json(self.cfg.out.with_name("quest.json")) or {}).get("seen") or {}
        now = now_iso()
        ids = {i["id"] for i in items}
        unreadable = {i["project"] for i in items if i["id"].startswith("step:error:")}
        was = {i["id"]: i for i in (previous or {}).get("items") or []}
        kept = {k: v for k, v in seen.items() if k not in ids and (was.get(k) or {}).get("project") in unreadable}
        seen = {**{i: seen.get(i, now) for i in ids}, **kept}
        for i in items:
            i["since"] = seen[i["id"]]
        try:
            write_json(f, seen)
        except OSError as e:
            print(f"items state not written: {e}", file=sys.stderr)
        return items

    # ---- the run ----------------------------------------------------------------------------
    def collect(self):
        """The whole JSON; writes nothing but the state files."""
        cfg = self.cfg
        envs = {}   # owner -> (env, error): one token per owner, i.e. per App
        for r, _ in cfg.projects:
            o = r.split("/")[0]
            if o not in envs:
                envs[o] = self.gh_env(o)
        previous = read_json(cfg.out)
        if not isinstance(previous, dict):
            previous = None
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            data = {
                "generated": now_iso(),
                "projects": list(pool.map(lambda rn: self.project(rn[0], rn[1], *envs[rn[0].split("/")[0]]), cfg.projects)),
                "bots": [self.bot(b) for b in cfg.bots],
                "guards": [self.report(m) for m in cfg.machines],
                "machine": self.machine(),
            }
            data["history"] = self.history(envs, previous, pool)
        data["items"] = self.since(self.items(data), previous)
        names = setmod.names(cfg.page_sets, cfg.assets)
        data["page"] = {"set": cfg.page_set, "sets": names, "labels": cfg.labels, "owner": cfg.owner, "fleet": cfg.fleet,
                        "personas": {b["name"]: b["persona"] for b in cfg.bots if b.get("persona")}}
        data["sets"] = setmod.run_all(names, data, previous, cfg)
        if all(e is None for e, _ in envs.values()):
            data["error"] = "fleet-token: no token for any owner"
        return data

    def summary(self, data, seconds):
        errs = [p["name"] for p in data["projects"] if p.get("error")]
        return (f"fleet.json written in {seconds:.1f} s: {len(data['projects'])} projects ({len(errs)} without access: {', '.join(errs) or '-'}), "
                f"{len(data['bots'])} bots, {len(data['items'])} items, {len(data['sets'])} sets; "
                f"{self.calls['gh']} GitHub calls, {self.calls['search']} of them searches")


def main(cfg, run=None):
    """One run: the JSON written to cfg.out, 0644, atomically; the summary line on stdout."""
    t0 = time.monotonic()
    c = Collector(cfg, run)
    data = c.collect()
    write_json(cfg.out, data, mode=0o644)
    print(c.summary(data, time.monotonic() - t0))
    return 0
