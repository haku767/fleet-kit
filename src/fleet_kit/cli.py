"""The fleet command."""

import argparse
import os
import sys

from . import __version__, labels, model, render, ruleset
from .github import GH, GHError

DEFAULT_CONF_DIR = "/etc/fleet/bots"
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
    if args.what != "conf":
        print(f"fleet apply: only `fleet apply conf` so far ({NEXT})", file=sys.stderr)
        return 2
    doc = load_checked(args.file, "apply")
    if doc is None:
        return 1
    if args.dir == DEFAULT_CONF_DIR and os.geteuid() != 0 and not args.dry_run:
        print(f"fleet apply conf: writing {DEFAULT_CONF_DIR} needs root (sudo), or --dir for a trial", file=sys.stderr)
        return 1
    actions = render.apply_conf(render.render(doc), args.dir, dry_run=args.dry_run)
    for what, path in actions:
        if what != "kept" or args.verbose:
            print(f"{'would be ' if args.dry_run else ''}{what}: {path}")
    n = sum(1 for w, _ in actions if w != "kept")
    print(f"fleet apply conf: {n} change{'s' if n != 1 else ''}, {len(actions) - n} unchanged, in {args.dir}"
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
    a = sub.add_parser("apply", help="put the rendered files on this machine: `apply conf` writes /etc/fleet/bots (sudo)")
    a.add_argument("what", metavar="conf")
    a.add_argument("file", nargs="?", default="fleet.toml")
    a.add_argument("--dir", default=DEFAULT_CONF_DIR, help=f"directory for the .conf files (default {DEFAULT_CONF_DIR})")
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
    for name, help_ in (("doctor", "what is wrong on this machine and on GitHub"), ("status", "the page's lists in the terminal"),
                        ("add-project", "append a project"), ("add-bot", "append a bot"),
                        ("page", "install collector and page")):
        p = sub.add_parser(name, help=f"{help_} ({NEXT.split(':')[0]})")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_later)
    args = ap.parse_args(argv)
    return args.func(args)
