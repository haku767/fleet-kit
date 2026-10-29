"""The fleet command."""

import argparse
import os
import sys

from . import __version__, doctor, edit, labels, model, render, ruleset
from .github import GH, GHError

DEFAULT_CONF_DIR = "/etc/fleet/bots"
DEFAULT_LISTS_DIR = "/etc/fleet"
NEXT = "not yet: fleet PLANS 13, stage 3"


def load_checked(file, cmd):
    """The file, loaded and checked; None after printing what is wrong."""
    try:
        text, doc = model.load(file)
    except FileNotFoundError:
        print(f"fleet {cmd}: {file}: no such file", file=sys.stderr)
        return None
    except Exception as e:  # tomllib.TOMLDecodeError, UnicodeDecodeError
        print(f"fleet {cmd}: {file}: {e}", file=sys.stderr)
        return None
    errors = [x for x in model.check(text, doc) if x.level == "error"]
    for x in errors:
        print(x, file=sys.stderr)
    if errors:
        print(f"fleet {cmd}: {file} has errors, run fleet check", file=sys.stderr)
        return None
    return doc


def cmd_check(args):
    try:
        text, doc = model.load(args.file)
    except FileNotFoundError:
        print(f"fleet check: {args.file}: no such file", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"fleet check: {args.file}: {e}", file=sys.stderr)
        return 2
    findings = model.check(text, doc)
    for x in findings:
        print(x)
    errors = sum(1 for x in findings if x.level == "error")
    warns = len(findings) - errors
    state = "ok" if not errors else f"{errors} error{'s' if errors != 1 else ''}"
    if warns:
        state += f", {warns} warning{'s' if warns != 1 else ''}"
    print(f"{args.file}: {model.summary(doc)}: {state}")
    return 1 if errors else 0


def cmd_render(args):
    doc = load_checked(args.file, "render")
    if doc is None:
        return 1
    files = render.render(doc)
    if args.bot:
        key = f"bots/{args.bot}.conf"   # by account, or by register name
        if key not in files:
            key = next((k for k, t in files.items() if k.startswith("bots/") and f"name='{args.bot}'" in t), None)
        if key is None:
            print(f"fleet render: no bot {args.bot}", file=sys.stderr)
            return 1
        sys.stdout.write(files[key])
        return 0
    for rel, text in files.items():
        path = os.path.join(args.out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        print(path)
    return 0


def cmd_apply(args):
    if args.what not in ("conf", "lists"):
        print(f"fleet apply: `fleet apply conf` and `fleet apply lists` so far ({NEXT})", file=sys.stderr)
        return 2
    doc = load_checked(args.file, "apply")
    if doc is None:
        return 1
    default_dir = DEFAULT_CONF_DIR if args.what == "conf" else DEFAULT_LISTS_DIR
    directory = args.dir or default_dir
    if directory == default_dir and os.geteuid() != 0 and not args.dry_run:
        print(f"fleet apply {args.what}: writing {default_dir} needs root (sudo), or --dir for a trial", file=sys.stderr)
        return 1
    files = render.render(doc)
    if args.what == "conf":
        actions = render.apply_conf(files, directory, dry_run=args.dry_run)
    else:
        machine = args.machine or os.uname().nodename
        if machine not in (doc.get("machines") or {}):
            print(f"fleet apply lists: this host is `{machine}`, not a machine in the file; --machine names one", file=sys.stderr)
            return 1
        actions = render.apply_lists(files, machine, directory, dry_run=args.dry_run)
        if not any(rel.startswith(f"machines/{machine}/") for rel in files):
            print(f"fleet apply lists: {machine} has no bots and no courier, nothing to write")
    for what, path in actions:
        if what != "kept" or args.verbose:
            print(f"{'would be ' if args.dry_run else ''}{what}: {path}")
    n = sum(1 for w, _ in actions if w != "kept")
    print(f"fleet apply {args.what}: {n} change{'s' if n != 1 else ''}, {len(actions) - n} unchanged, in {directory}"
          + (" (dry run)" if args.dry_run else ""))
    return 0


def projects_for(doc, name, cmd):
    """The projects a GitHub command works on: one by name, or `all` with bots."""
    projects = doc.get("projects") or {}
    if name == "all":
        return [n for n, p in projects.items() if p.get("plan") or p.get("dev")]
    if name not in projects:
        print(f"fleet {cmd}: no project {name} in the file", file=sys.stderr)
        return None
    return [name]


def cmd_labels(args):
    doc = load_checked(args.file, "labels")
    if doc is None:
        return 1
    names = projects_for(doc, args.project, "labels")
    if names is None:
        return 1
    gh = GH()
    failed = todo = 0
    for pname in names:
        p = doc["projects"][pname]
        repo = p["repo"]
        if not (p.get("plan") or p.get("dev")):
            print(f"{pname} ({repo}): no bots, no fleet labels wanted")
            continue
        wanted = labels.desired(doc, pname)
        try:
            have = labels.fetch(gh, repo)
            pl = labels.plan(wanted, have)
            print(f"{pname} ({repo}): {len(pl['create'])} to create, {len(pl['update'])} to change, "
                  f"{len(pl['ok'])} as wanted, {len(pl['other'])} other label(s) left alone")
            for line in labels.describe(pl):
                print(line)
            if args.verbose and pl["other"]:
                print("  other   " + ", ".join(pl["other"]))
            if args.apply and (pl["create"] or pl["update"]):
                n = labels.apply(gh, repo, pl)
                print(f"  done: {n} label(s) written")
            todo += len(pl["create"]) + len(pl["update"])
        except GHError as e:
            print(f"{pname} ({repo}): {e}", file=sys.stderr)
            failed += 1
    if todo and not args.apply:
        print("fleet labels: nothing written; add --apply to create and change them (needs write access to the repository)")
    return 1 if failed else 0


def cmd_ruleset(args):
    doc = load_checked(args.file, "ruleset")
    if doc is None:
        return 1
    names = projects_for(doc, args.project, "ruleset")
    if names is None:
        return 1
    gh = GH()
    failed = todo = 0
    for pname in names:
        p = doc["projects"][pname]
        repo = p["repo"]
        try:
            have_all = ruleset.fetch(gh, repo)
        except GHError as e:
            print(f"{pname} ({repo}): {e}", file=sys.stderr)
            failed += 1
            continue
        have = next((r for r in have_all if r.get("name") == ruleset.RULESET_NAME), None)
        others = [r["name"] for r in have_all if r is not have]
        if "reviews" not in p:
            state = f"has `{ruleset.RULESET_NAME}`" if have else "has none"
            print(f"{pname} ({repo}): no `reviews` in the file, no ruleset wanted; the repository {state}"
                  + (f", also {', '.join(others)}" if others else ""))
            continue
        want = ruleset.wanted(p["reviews"])
        d = ruleset.diff(want, have)
        real = [x for x in d if not x.endswith("(kept)")]
        print(f"{pname} ({repo}): ruleset `{ruleset.RULESET_NAME}` on the default branch, {p['reviews']} approving review(s): "
              + ("as wanted" if not real else ("missing" if have is None else f"{len(real)} difference(s)")))
        for x in d:
            if x != "missing":
                print(f"  {x}")
        if others:
            print(f"  other ruleset(s) left alone: {', '.join(others)}")
        if real:
            todo += 1
            if args.apply:
                try:
                    print(f"  {ruleset.apply(gh, repo, want, have)}")
                except GHError as e:
                    print(f"{pname} ({repo}): {e}", file=sys.stderr)
                    failed += 1
    if todo and not args.apply:
        print("fleet ruleset: nothing written; add --apply to create or change it (needs admin rights on the repository: the owner)")
    return 1 if failed else 0


def read_for_edit(file, cmd):
    """Text and document of a file about to be appended to; None after printing what is wrong."""
    try:
        text, doc = model.load(file)
    except FileNotFoundError:
        print(f"fleet {cmd}: {file}: no such file", file=sys.stderr)
        return None
    except Exception as e:
        print(f"fleet {cmd}: {file}: {e}", file=sys.stderr)
        return None
    if any(x.level == "error" for x in model.check(text, doc)):
        print(f"fleet {cmd}: {file} has errors already, run fleet check first", file=sys.stderr)
        return None
    return text, doc


def finish_edit(args, text, doc_before, cmd, what, pname, kinds, machine):
    findings = edit.write_checked(args.file, text, cmd)
    for x in findings:
        print(x, file=sys.stderr)
    if any(x.level == "error" for x in findings):
        print(f"fleet {cmd}: that would leave {args.file} with errors; nothing written", file=sys.stderr)
        return 1
    print(f"fleet {cmd}: {what} in {args.file}")
    _, doc = model.load(args.file)
    print()
    print("\n".join(edit.github_steps(doc, pname, kinds, machine)))
    return 0


def default_machine(doc, wanted=None):
    machines = doc.get("machines") or {}
    if wanted:
        return wanted
    bots = [m for m, v in machines.items() if v.get("role") == "bots"]
    return bots[0] if len(bots) == 1 else None


def cmd_add_project(args):
    loaded = read_for_edit(args.file, "add-project")
    if loaded is None:
        return 1
    text, doc = loaded
    name = args.name
    if name in (doc.get("projects") or {}):
        print(f"fleet add-project: {name} is in the file already", file=sys.stderr)
        return 1
    if not model.ACCOUNT.match(name):
        print("fleet add-project: the name must be lowercase letters, digits, - or _ (it names the bots' accounts)", file=sys.stderr)
        return 1
    if not args.stack and not args.test:
        print("fleet add-project: --stack <stack> (from [stacks]) or --test <command> is required", file=sys.stderr)
        return 1
    machine = default_machine(doc, args.machine)
    if machine is None and (not args.no_plan or not args.no_dev):
        print("fleet add-project: several machines have role bots; --machine names one", file=sys.stderr)
        return 1
    fields = {"repo": args.repo, "stacks": args.stack or None, "test": args.test, "ci": args.ci,
              "reviews": args.reviews, "machine": args.machine, "paused": True if args.paused else None}
    kinds = []
    if not args.no_plan:
        fields["plan"] = {"name": args.plan or f"{name}-plan"}
        kinds.append("plan")
    if not args.no_dev:
        fields["dev"] = {"name": args.dev or f"{name}-dev"}
        kinds.append("dev")
    block = edit.project_block(name, fields)
    bots = ", ".join(fields[k]["name"] for k in kinds)
    what = f"appended [projects.{name}] ({args.repo}" + (f", bots {bots}" if bots else ", no bots") + ")"
    return finish_edit(args, edit.insert_project(text, block), doc, "add-project", what, name, kinds, machine)


def cmd_add_bot(args):
    loaded = read_for_edit(args.file, "add-bot")
    if loaded is None:
        return 1
    text, doc = loaded
    p = (doc.get("projects") or {}).get(args.project)
    if p is None:
        print(f"fleet add-bot: no project {args.project} in the file", file=sys.stderr)
        return 1
    if args.kind in p:
        print(f"fleet add-bot: {args.project} has a {args.kind} bot already ({p[args.kind].get('name')})", file=sys.stderr)
        return 1
    machine = default_machine(doc, args.machine or p.get("machine"))
    if machine is None:
        print("fleet add-bot: several machines have role bots; --machine names one", file=sys.stderr)
        return 1
    spec = {"name": args.name or f"{args.project}-{args.kind}"}
    for k in ("account", "persona", "machine"):
        if getattr(args, k):
            spec[k] = getattr(args, k)
    what = f"added {args.kind} = {edit.toml_value(spec)} to [projects.{args.project}]"
    return finish_edit(args, edit.insert_bot(text, args.project, args.kind, spec), doc, "add-bot", what,
                       args.project, [args.kind], machine)


def cmd_doctor(args):
    doc = load_checked(args.file, "doctor")
    if doc is None:
        return 1
    names = projects_for(doc, args.project, "doctor")
    if names is None:
        return 1
    findings = doctor.run(GH(), doc, names)
    for x in findings:
        if x.level != "ok" or args.verbose:
            print(x)
    n = {lv: sum(1 for x in findings if x.level == lv) for lv in ("ok", "warn", "fail", "skip")}
    print(f"fleet doctor (GitHub): {len(findings)} checks: {n['ok']} ok, {n['warn']} warn, {n['fail']} fail, {n['skip']} skipped"
          + ("" if args.verbose else "; -v lists the ok ones") + ". The machine half is not in the kit yet (PLANS 13 stage 3e).")
    return 1 if n["fail"] else 0


def cmd_later(args):
    print(f"fleet {args.command}: {NEXT}", file=sys.stderr)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fleet", description="one fleet.toml and the fleet around it")
    ap.add_argument("--version", action="version", version=f"fleet-kit {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="validate fleet.toml")
    c.add_argument("file", nargs="?", default="fleet.toml")
    c.set_defaults(func=cmd_check)
    r = sub.add_parser("render", help="the derived files: one .conf per bot, the lists, the page's tables")
    r.add_argument("file", nargs="?", default="fleet.toml")
    r.add_argument("--out", default="rendered", help="directory to write into (default rendered/)")
    r.add_argument("--bot", help="print this bot's .conf to stdout instead")
    r.set_defaults(func=cmd_render)
    a = sub.add_parser("apply", help="put the rendered files on this machine (sudo): `apply conf` writes /etc/fleet/bots, "
                                     "`apply lists` /etc/fleet/lists.sh and courier.sh")
    a.add_argument("what", metavar="conf|lists")
    a.add_argument("file", nargs="?", default="fleet.toml")
    a.add_argument("--dir", help=f"directory to write into (default {DEFAULT_CONF_DIR} for conf, {DEFAULT_LISTS_DIR} for lists)")
    a.add_argument("--machine", help="for lists: the machine from the file (default: this host's name)")
    a.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    a.add_argument("-v", "--verbose", action="store_true", help="list the unchanged files too")
    a.set_defaults(func=cmd_apply)
    for name, help_, fn in (("labels", "the fleet's labels in a project's repository (gh api)", cmd_labels),
                            ("ruleset", "the rule on a project's default branch (gh api, the owner)", cmd_ruleset)):
        g = sub.add_parser(name, help=help_)
        g.add_argument("project", help="a project from the file, or `all` (every project with bots)")
        g.add_argument("file", nargs="?", default="fleet.toml")
        g.add_argument("--apply", action="store_true", help="write to GitHub; without it the command only compares")
        g.add_argument("-v", "--verbose", action="store_true", help="list the repository's other labels too")
        g.set_defaults(func=fn)
    ap_ = sub.add_parser("add-project", help="append a project (and its two bots) to the file; prints what GitHub and the machine need")
    ap_.add_argument("name", help="the project's name (lowercase; the bots default to <name>-plan and <name>-dev)")
    ap_.add_argument("--repo", required=True, help="owner/name on GitHub")
    ap_.add_argument("--stack", action="append", help="a stack from [stacks]; repeat for several")
    ap_.add_argument("--test", help="the project's own test command, in place of or besides the stacks'")
    ap_.add_argument("--ci", choices=model.CI, help="yes | no | auto")
    ap_.add_argument("--reviews", type=int, help="approving reviews the default branch's ruleset asks for (fleet ruleset)")
    ap_.add_argument("--plan", metavar="NAME", help="the plan bot's name")
    ap_.add_argument("--dev", metavar="NAME", help="the dev bot's name")
    ap_.add_argument("--no-plan", action="store_true", help="no plan bot")
    ap_.add_argument("--no-dev", action="store_true", help="no dev bot")
    ap_.add_argument("--machine", help="the bots' machine when several have role bots")
    ap_.add_argument("--paused", action="store_true", help="start paused (no rounds)")
    ap_.add_argument("file", nargs="?", default="fleet.toml")
    ap_.set_defaults(func=cmd_add_project)
    ab = sub.add_parser("add-bot", help="append a plan or dev bot to a project in the file; prints what GitHub and the machine need")
    ab.add_argument("project")
    ab.add_argument("kind", choices=("plan", "dev"))
    ab.add_argument("--name", help="the bot's name (default <project>-<kind>)")
    ab.add_argument("--account", help="the Unix account when it differs from the name")
    ab.add_argument("--persona", help="the page's persona")
    ab.add_argument("--machine", help="the bot's machine when several have role bots")
    ab.add_argument("file", nargs="?", default="fleet.toml")
    ab.set_defaults(func=cmd_add_bot)
    d = sub.add_parser("doctor", help="what is wrong on GitHub for a project: repository, labels, ruleset, Apps, installations, "
                                      "who set ready, the runner (the machine half follows)")
    d.add_argument("project", nargs="?", default="all", help="a project from the file, or `all` (default)")
    d.add_argument("file", nargs="?", default="fleet.toml")
    d.add_argument("-v", "--verbose", action="store_true", help="list the checks that are ok too")
    d.set_defaults(func=cmd_doctor)
    for name, help_ in (("status", "the page's lists in the terminal"), ("page", "install collector and page")):
        p = sub.add_parser(name, help=f"{help_} ({NEXT.split(':')[0]})")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_later)
    args = ap.parse_args(argv)
    return args.func(args)
