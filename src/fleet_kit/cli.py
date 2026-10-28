"""The fleet command."""

import argparse
import os
import sys

from . import __version__, model, render

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
    for name, help_ in (("doctor", "what is wrong on this machine and on GitHub"), ("status", "the page's lists in the terminal"),
                        ("add-project", "append a project"), ("add-bot", "append a bot"),
                        ("labels", "create a project's labels"), ("ruleset", "the default branch's ruleset"),
                        ("page", "install collector and page")):
        p = sub.add_parser(name, help=f"{help_} ({NEXT.split(':')[0]})")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_later)
    args = ap.parse_args(argv)
    return args.func(args)
