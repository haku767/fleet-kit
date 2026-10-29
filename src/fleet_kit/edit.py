"""`fleet add-project` and `fleet add-bot`: append to fleet.toml, then say what GitHub and
the machine still need.

The file is text with comments, so nothing is re-serialized: a new `[projects.<name>]` block
goes in before the first `[guards.` or `[people.` header (else at the end), a new bot is one
`plan = {...}` or `dev = {...}` line at the end of its project's block. The result is checked
before it is written; a file with errors is never written."""

import os
import re
import tempfile

from . import model

HEADER = re.compile(r"^\[(?P<name>[^\]]+)\]\s*(#.*)?$")
PERMISSIONS = ("Contents read/write", "Issues read/write", "Pull requests read/write", "Metadata read",
               "Checks read", "Actions read")


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def project_block(name, fields):
    """`[projects.<name>]` with the given keys, in the order given."""
    lines = [f"[projects.{name}]"]
    for k, v in fields.items():
        if v is None:
            continue
        key = k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else toml_value(k)
        lines.append(f"{key} = {toml_value(v)}")
    return "\n".join(lines) + "\n"


def table_spans(text):
    """(name, first line index, line index after the last line) per table header, in order."""
    lines = text.splitlines(keepends=True)
    heads = [(i, m.group("name").strip()) for i, l in enumerate(lines) if (m := HEADER.match(l.strip()))]
    spans = []
    for n, (i, name) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        spans.append((name, i, end))
    return lines, spans


def insert_project(text, block):
    """The text with the block inserted before the first [guards.*] or [people.*] table."""
    lines, spans = table_spans(text)
    at = next((i for name, i, _ in spans if name.split(".")[0] in ("guards", "people")), None)
    if at is None:
        if text and not text.endswith("\n"):
            text += "\n"
        return text + "\n" + block
    # keep a comment that leads the next table with that table
    while at > 0 and lines[at - 1].lstrip().startswith("#"):
        at -= 1
    before, after = "".join(lines[:at]), "".join(lines[at:])
    if not before.endswith("\n\n"):
        before += "\n"
    return before + block + "\n" + after


def insert_bot(text, pname, kind, spec):
    """The text with `<kind> = {...}` at the end of [projects.<pname>]'s own lines."""
    lines, spans = table_spans(text)
    span = next(((i, end) for name, i, end in spans if name == f"projects.{pname}"), None)
    if span is None:
        raise KeyError(pname)
    i, end = span
    last = end
    while last > i + 1 and (not lines[last - 1].strip() or lines[last - 1].lstrip().startswith("#")):
        last -= 1   # blank lines and the comment leading the next table stay after the new line
    line = f"{kind} = {toml_value(spec)}\n"
    return "".join(lines[:last]) + line + "".join(lines[last:])


def write_checked(path, text, cmd):
    """Checks the text as the file; writes it atomically when clean. Returns the findings."""
    import tomllib
    doc = tomllib.loads(text)
    findings = model.check(text, doc)
    if any(x.level == "error" for x in findings):
        return findings
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".fleet-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    except FileNotFoundError:
        pass
    os.replace(tmp, path)
    return findings


def github_steps(doc, pname, kinds, machine):
    """What the owner still does by hand after the file knows the project: lines of text."""
    fleet = doc["fleet"]
    owner = fleet["owner"]
    p = doc["projects"][pname]
    repo = p["repo"]
    repo_owner = repo.split("/")[0]
    bots = [(k, p[k]["name"], p[k].get("account") or p[k]["name"]) for k in kinds if isinstance(p.get(k), dict)]
    apps = [p[k].get("app") or f"{owner}-{p[k]['name']}" for k, _, _ in bots]
    m = (doc.get("machines") or {}).get(machine) or {}
    home = m.get("home_root", "/srv")
    out = [f"Next, for {pname} ({repo}):", ""]
    n = 0
    if apps:
        n += 1
        out += [f"{n}. On GitHub, Settings > Developer settings > GitHub Apps > New GitHub App, one per bot: "
                + ", ".join(f"`{a}`" for a in apps) + ".",
                "   Webhook off. Repository permissions: " + ", ".join(PERMISSIONS) + ".",
                "   Generate a private key for each (the .pem) and note the App ID."]
        n += 1
        where = f"your account" if repo_owner == owner else f"{repo_owner}'s account ({repo_owner} installs it)"
        reads = [doc["projects"][r]["repo"] for r in p.get("reads") or [] if r in doc["projects"]]
        out.append(f"{n}. Install each App on {where}, restricted to {repo}"
                   + (f"; read-only on {', '.join(reads)} too (`reads`)" if reads else "") + ".")
        if repo_owner != owner:
            out.append(f"   The repository belongs to {repo_owner}: a guard needs a second App there to read it for the page "
                       f"(`owner_app`, see the check's warning).")
    n += 1
    out.append(f"{n}. Labels: `fleet labels {pname} --apply` (any login or App with write access to the repository).")
    if "reviews" in p:
        n += 1
        out.append(f"{n}. Ruleset: `fleet ruleset {pname} --apply` (admin rights: you).")
    if bots:
        n += 1
        out += ["", f"On {machine or 'the bots machine'} (root):", "",
                f"{n}. The accounts " + ", ".join(f"`{a}`" for _, _, a in bots)
                + f" with their homes under {home}, inboxes and timers: your install script with the lists from "
                  "`fleet apply lists` (`fleet apply` takes this over in PLANS 13 stage 3f)."]
        n += 1
        out.append(f"{n}. Each bot's App: `{home}/<account>/.config/fleet/app-id` (the ID, one line) and `app.pem` (mode 600), "
                   "owned by the account; then `claude` once as the account to sign in.")
        n += 1
        out.append(f"{n}. `sudo fleet apply conf` and `sudo fleet apply lists`; one round by hand per bot "
                   "(`bot-round`, `bot-review`), then the timers.")
        n += 1
        out.append(f"{n}. The repository: `AGENTS.md` with the test command and the house rules; the plan bot's first issues with "
                   "acceptance criteria, the first one labelled `ready`.")
    return out
