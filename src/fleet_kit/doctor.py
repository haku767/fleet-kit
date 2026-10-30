"""`fleet doctor`, the GitHub half: what the guards' checks look at on GitHub, per project.

One Finding per check, levels ok | warn | fail: fail is what a round trips on (repository
not reachable, an App missing, a `ready` set by someone not trusted, a wanted ruleset
missing), warn is what should be put right but does not stop a round (label wording, a
permission short, a ruleset that differs). Some checks need the owner's login (installations,
runners): run as a bot they say so and are skipped, not failed. The machine half (accounts,
timers, status files, inboxes) is ws-guard's, PLANS 13 stage 3e."""

from dataclasses import dataclass

from . import labels, render, ruleset
from .github import GHError

# what a project bot's App needs (docs/eigene-fleet.md step 2); checks and actions read only
# where there is CI to read (`ci` yes or auto); a guard's App reads only
BOT_PERMISSIONS = {"contents": "write", "issues": "write", "pull_requests": "write", "metadata": "read"}
CI_PERMISSIONS = {"checks": "read", "actions": "read"}
GUARD_PERMISSIONS = {"contents": "read", "issues": "read", "pull_requests": "read", "metadata": "read"}
LEVELS = {"read": 1, "write": 2, "admin": 3}


@dataclass
class Finding:
    level: str      # ok | warn | fail | skip
    where: str
    message: str

    def __str__(self):
        return f"{self.level}: {self.where}: {self.message}"


class Caller:
    """Who runs the command: a user (login) or an App's installation token (login None)."""

    def __init__(self, gh):
        self.gh = gh
        self._installations = None
        self.unusable = None   # the GHError when gh itself cannot talk to GitHub (no token, not installed)
        try:
            self.login = gh.get("user")["login"]
        except GHError as e:
            self.login = None
            if any(k in str(e) for k in ("gh auth login", "GH_TOKEN", "not installed")):
                self.unusable = e
        except (KeyError, TypeError):
            self.login = None

    def installations(self):
        """app slug -> set of full repository names it is installed on, for the caller's account."""
        if self._installations is None:
            self._installations = {}
            for inst in self.gh.get("user/installations?per_page=100", paginate=True):
                repos = self.gh.get(f"user/installations/{inst['id']}/repositories?per_page=100", paginate=True)
                self._installations[inst["app_slug"]] = {r["full_name"] for r in repos}
        return self._installations


def permissions_short(have, want):
    return [f"{k} {v}" for k, v in want.items() if LEVELS.get(have.get(k), 0) < LEVELS[v]]


def check_app(gh, caller, owner, slug, want, where):
    try:
        app = gh.get(f"apps/{slug}")
    except GHError as e:
        invisible = "Not Found" in str(e) or "not accessible" in str(e)
        if invisible and caller.login == owner:
            return Finding("fail", where, f"App {slug} does not exist")
        if invisible:
            return Finding("skip", where, f"App {slug}: not visible to you (a private App shows only to its owner)")
        return Finding("fail", where, f"App {slug}: {e}")
    short = permissions_short(app.get("permissions") or {}, want)
    if short:
        return Finding("warn", where, f"App {slug} lacks {', '.join(short)}")
    return Finding("ok", where, f"App {slug} exists with the permissions it needs")


def check_installed(caller, owner, slug, repos, where):
    if caller.login is None:
        return Finding("skip", where, f"installation of {slug}: only the owner's login sees installations")
    if caller.login != owner:
        return Finding("skip", where, f"installation of {slug}: you are {caller.login}, the installations are {owner}'s")
    have = caller.installations().get(slug)
    if have is None:
        return Finding("fail", where, f"App {slug} is not installed on {owner}'s account")
    missing = [r for r in repos if r not in have]
    if missing:
        return Finding("fail", where, f"App {slug} is installed but not on {', '.join(missing)}")
    return Finding("ok", where, f"App {slug} is installed on {', '.join(repos)}")


def paged(gh, path):
    return gh.get(path + ("&" if "?" in path else "?") + "per_page=100", paginate=True)


def check_ready(gh, repo, trusted, where):
    try:
        issues = paged(gh, f"repos/{repo}/issues?labels=ready&state=open")
    except GHError as e:
        return [Finding("fail", where, f"ready issues: {e}")]
    issues = [i for i in issues if "pull_request" not in i]
    out = []
    bad = 0
    for i in issues:
        try:
            events = paged(gh, f"repos/{repo}/issues/{i['number']}/timeline")
        except GHError as e:
            out.append(Finding("fail", where, f"#{i['number']}: timeline: {e}"))
            continue
        who = [e.get("actor", {}).get("login") for e in events
               if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == "ready"]
        last = who[-1] if who else None
        if last not in trusted:
            bad += 1
            out.append(Finding("fail", where, f"#{i['number']} `ready` was set by {last or 'nobody on record'}, "
                                              f"not one of {', '.join(trusted)}; the dev bot must not build it"))
    if not issues:
        out.append(Finding("ok", where, "no open ready issue"))
    elif not bad:
        out.append(Finding("ok", where, f"{len(issues)} open ready issue(s), all set by someone trusted"))
    return out


def check_runner(gh, caller, owner, repo, name, where):
    if caller.login != owner:
        return Finding("skip", where, f"runner {name}: only the owner's login lists runners")
    try:
        runners = paged(gh, f"repos/{repo}/actions/runners")
    except GHError as e:
        return Finding("fail", where, f"runners: {e}")
    r = next((x for x in runners if x.get("name") == name), None)
    if r is None:
        return Finding("fail", where, f"runner {name} is not registered on {repo}")
    if r.get("status") != "online":
        return Finding("warn", where, f"runner {name} is {r.get('status')}")
    return Finding("ok", where, f"runner {name} is online")


def project_checks(gh, caller, doc, pname):
    fl = render.Fleet(doc)
    p = doc["projects"][pname]
    repo = p["repo"]
    owner = fl.owner
    out = []
    try:
        info = gh.get(f"repos/{repo}")
    except GHError as e:
        # a repository under another owner is invisible to the fleet owner's tokens: the doctor
        # runs with one token, so that is a skip naming the token to run with, not a fault; a
        # repository under the fleet's own owner that cannot be seen is a fault
        invisible = "Not Found" in str(e) or "not accessible" in str(e)
        if invisible and repo.split("/")[0].lower() != (owner or "").lower():
            app = p.get("owner_app")
            how = (f"GH_TOKEN=$(fleet-token --app {app}) fleet doctor {pname}" if app else
                   f"a token that sees {repo.split('/')[0]}'s repositories, then fleet doctor {pname}")
            return [Finding("skip", pname, f"{repo} is under another owner and this token cannot see it: run {how}")]
        return [Finding("fail", pname, f"{repo}: {e}")]
    out.append(Finding("ok", pname, f"{repo} reachable, default branch {info.get('default_branch')}"
               + (", archived!" if info.get("archived") else "")))
    # labels
    try:
        pl = labels.plan(labels.desired(doc, pname), labels.fetch(gh, repo))
        n = len(pl["create"]) + len(pl["update"])
        out.append(Finding("ok" if not n else "warn", pname,
                           "labels as wanted" if not n else
                           f"labels: {len(pl['create'])} to create, {len(pl['update'])} to change (fleet labels {pname})"))
    except GHError as e:
        out.append(Finding("fail", pname, f"labels: {e}"))
    # ruleset
    try:
        have_all = ruleset.fetch(gh, repo)
        have = next((r for r in have_all if r.get("name") == ruleset.RULESET_NAME), None)
        if "reviews" in p:
            d = [x for x in ruleset.diff(ruleset.wanted(p["reviews"]), have) if not x.endswith("(kept)")]
            if have is None:
                out.append(Finding("fail", pname, f"ruleset {ruleset.RULESET_NAME} missing (fleet ruleset {pname}, the owner)"))
            elif d:
                out.append(Finding("warn", pname, f"ruleset {ruleset.RULESET_NAME} differs: {'; '.join(d)} (fleet ruleset {pname})"))
            else:
                out.append(Finding("ok", pname, f"ruleset {ruleset.RULESET_NAME} as wanted ({p['reviews']} approving review(s))"))
        else:
            out.append(Finding("ok", pname, "no ruleset wanted" + (f"; the repository has {ruleset.RULESET_NAME}" if have else "")))
    except GHError as e:
        out.append(Finding("warn", pname, f"rulesets: {e}"))
    # the bots' Apps and their installations
    reads = [doc["projects"][r]["repo"] for r in p.get("reads") or [] if r in doc["projects"]]
    for kind in ("plan", "dev"):
        b = p.get(kind)
        if not isinstance(b, dict) or not b.get("name"):
            continue
        slug = fl.app(b)
        want = dict(BOT_PERMISSIONS, **(CI_PERMISSIONS if p.get("ci", "auto") != "no" else {}))
        out.append(check_app(gh, caller, owner, slug, want, f"{pname}.{kind}"))
        out.append(check_installed(caller, owner, slug, [repo] + reads, f"{pname}.{kind}"))
    # who set ready
    out += check_ready(gh, repo, fl.trusted(p).split(), pname)
    if p.get("runner"):
        out.append(check_runner(gh, caller, owner, repo, p["runner"], pname))
    return out


def guard_checks(gh, caller, doc):
    fl = render.Fleet(doc)
    out = []
    for gname, g in (doc.get("guards") or {}).items():
        for a in g.get("apps") or []:
            slug = a if isinstance(a, str) else a.get("app") or f"{a.get('owner')}-{a.get('name')}"
            out.append(check_app(gh, caller, fl.owner, slug, GUARD_PERMISSIONS, f"guards.{gname}"))
    return out


def run(gh, doc, names):
    caller = Caller(gh)
    if caller.unusable:
        return [Finding("skip", "caller", f"GitHub half skipped, gh has no token here: {caller.unusable} "
                                          "(run as a login with gh auth, or with GH_TOKEN from fleet-token)")]
    out = [Finding("ok", "caller", f"running as {caller.login}" if caller.login else
                   "running as an App token: installations and runners are skipped (run as the owner for those)")]
    for pname in names:
        out += project_checks(gh, caller, doc, pname)
    if names and len(names) > 1:
        out += guard_checks(gh, caller, doc)
    return out
