"""The fleet command."""

import argparse
import sys

from . import __version__, model

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
    for name, help_ in (("render", "the derived files"), ("apply", "put the rendered files on this machine (sudo)"),
                        ("doctor", "what is wrong on this machine and on GitHub"), ("status", "the page's lists in the terminal"),
                        ("add-project", "append a project"), ("add-bot", "append a bot"),
                        ("labels", "create a project's labels"), ("ruleset", "the default branch's ruleset"),
                        ("page", "install collector and page")):
        p = sub.add_parser(name, help=f"{help_} ({NEXT.split(':')[0]})")
        p.add_argument("rest", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_later)
    args = ap.parse_args(argv)
    return args.func(args)
