"""`fleet labels <project>`: the fleet's labels in a project's repository.

The set comes from `[labels]` in fleet.toml, or DEFAULT_LABELS when the file has none. A
description may hold {owner}, {project}, {plan} and {dev} (the bots' names; the owner when a
project lacks one). A label whose name holds {name} is the bot label: one per bot of the
project. Labels the repository has beyond that are left alone; nothing is ever deleted."""

from urllib.parse import quote

from . import model

# bot-round, bot-review and the page know these names; `needs-haku` is still the literal
# name in the scripts, whoever the owner is.
DEFAULT_LABELS = {
    "ready": {"color": "0e8a16", "description": "Acceptance criteria set; {dev} may build it"},
    "needs-haku": {"color": "d93f0b", "description": "a bot stopped here, {owner} decides"},
    "next": {"color": "0052cc", "description": "The ready issue to build first; set by {plan}"},
    "live": {"color": "5319e7", "description": "Needs {owner}'s desktop or the running program to build or to see the fault; the rounds skip it"},
    "size:S": {"color": "c2e0c6", "description": "Small: one file or one function; Sonnet"},
    "size:M": {"color": "fbca04", "description": "Medium: a module and its test; Opus"},
    "size:L": {"color": "d93f0b", "description": "Large: several modules or a new test harness; Opus"},
    "bot:{name}": {"color": "ededed", "description": "written by or for the bot {name}"},
}


def label_set(doc):
    t = doc.get("labels")
    return t if isinstance(t, dict) and t else DEFAULT_LABELS


def desired(doc, pname):
    """The labels a project's repository should have: list of {name, color, description}."""
    p = doc["projects"][pname]
    owner = doc["fleet"]["owner"]
    names = {kind: (p.get(kind) or {}).get("name") or owner for kind in ("plan", "dev")}
    fields = {"owner": owner, "project": pname, "plan": names["plan"], "dev": names["dev"]}
    bots = [b.name for b in model.bots(doc) if b.project == pname]
    out = []
    for name, spec in label_set(doc).items():
        color = str(spec.get("color", "ededed")).lower().lstrip("#")
        desc = spec.get("description", "")
        if "{name}" in name:
            for b in bots:
                out.append({"name": name.replace("{name}", b), "color": color,
                            "description": desc.replace("{name}", b).format_map(fields)})
        else:
            out.append({"name": name, "color": color, "description": desc.format_map(fields)})
    return out


def plan(wanted, existing):
    """What to do: dict with create, update (each (label, existing)), ok, other (names)."""
    have = {l["name"].lower(): l for l in existing}
    out = {"create": [], "update": [], "ok": [], "other": []}
    seen = set()
    for l in wanted:
        e = have.get(l["name"].lower())
        if e is None:
            out["create"].append(l)
            continue
        seen.add(l["name"].lower())
        same = (e.get("color", "").lower() == l["color"] and (e.get("description") or "") == l["description"]
                and e["name"] == l["name"])
        if same:
            out["ok"].append(l["name"])
        else:
            out["update"].append((l, e))
    out["other"] = sorted(l["name"] for l in existing if l["name"].lower() not in seen)
    return out


def describe(pl):
    """The plan as lines, one per label to create or change."""
    lines = [f"  create  {l['name']}  #{l['color']}  {l['description']}" for l in pl["create"]]
    for l, e in pl["update"]:
        diffs = []
        if e["name"] != l["name"]:
            diffs.append(f"name {e['name']!r} -> {l['name']!r}")
        if e.get("color", "").lower() != l["color"]:
            diffs.append(f"color #{e.get('color', '')} -> #{l['color']}")
        if (e.get("description") or "") != l["description"]:
            diffs.append(f"description {e.get('description') or ''!r} -> {l['description']!r}")
        lines.append(f"  update  {l['name']}: " + "; ".join(diffs))
    return lines


def fetch(gh, repo):
    return gh.get(f"repos/{repo}/labels?per_page=100", paginate=True)


def apply(gh, repo, pl):
    """Creates and updates; returns the number of calls made."""
    n = 0
    for l in pl["create"]:
        gh.post(f"repos/{repo}/labels", {"name": l["name"], "color": l["color"], "description": l["description"]})
        n += 1
    for l, e in pl["update"]:
        gh.patch(f"repos/{repo}/labels/{quote(e['name'], safe='')}",
                 {"new_name": l["name"], "color": l["color"], "description": l["description"]})
        n += 1
    return n
