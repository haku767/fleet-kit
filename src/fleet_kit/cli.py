"""The fleet command."""

import argparse
import sys

import os

from . import __version__, model, render

NEXT = "not yet: fleet PLANS 13, stage 2 (render, apply conf) and 3 (the rest)"


def cmd_check(args):
    try:
        text, doc = model.load(args.file)
    except FileNotFoundError:
        print(f"fleet check: {args.file}: no such file", file=sys.stderr)
        return 2
    except Exception as e:  # tomllib.TOMLDecodeError, UnicodeDecodeError
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


def load_checked(file, cmd):
    """The file, loaded and checked; None after printing what is wrong."""
    try:
        text, doc = model.load(file)
    except FileNotFoundError:
        print(f"fleet {cmd}: {file}: no such file", file=sys.stderr)
        return None
    except Exception as e:
        print(f"fleet {cmd}: {file}: {e}", file=sys.stderr)
        return None
    errors = [x for x in model.check(text, doc) if x.level == "error"]
    for x in errors:
        print(x, file=sys.stderr)
    if errors:
        print(f"fleet {cmd}: {file} has errors, run fleet check", file=sys.stderr)
        return None
    return doc


def cmd_render(args):
    doc = load_checked(args.file, "render")
    if doc is None:
        return 1
    files = render.render(doc)
    if args.bot:
        key = f"bots/{args.bot}.conf"
        if key not in files:
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
    for name, help_ in (("apply", "put the rendered files on this machine (sudo)"),
                        ("doctor", "what is wrong on this machine and on GitHub"), ("status", "the page's lists in the terminal"),
                        ("add-project", "append a project"), ("add-bot", "append a bot"),
                        ("labels", "create a project's labels"), ("ruleset", "the default branch's ruleset"),
                        ("page", "install collector and page")):
        p = sub.add_parser(name, help=f"{help_} ({NEXT.split(':')[0]})")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_later)
    args = ap.parse_args(argv)
    return args.func(args)
