"""fleet status: the page's shared part as text.

Reads the JSON `fleet collect` writes (`<agents_dir>/fleet/fleet.json`, or `--json F`) and
prints what the overview's lists say: merge, decide, step in, then one line per project and per
bot, and the machine with the guards. The same facts as the page, for a shell, an ssh session
or a panel; no set's block (the quest, the levels) is read. With a project's name only that
project's items and bots. Exit 1 when the JSON is missing or unreadable, 2 when it is older than
STALE_MIN minutes (the page shows the same item), else 0.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collect import BAD, DONE

STALE_MIN = 20
WORKING = {"running", "build", "fix", "review", "retry"}


def minutes_ago(iso):
    try:
        return max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() // 60))
    except (ValueError, AttributeError, TypeError):
        return None


def ago(iso):
    m = minutes_ago(iso)
    if m is None:
        return "-"
    return f"{m} min" if m < 60 else f"{round(m / 60)} h" if m < 2880 else f"{round(m / 1440)} d"


def bot_state(b, here):
    """The page's states: sleep, off, bad, work, done; `here` is the machine the JSON is from."""
    if b.get("machine") != here or b.get("rounds") == "none":
        return "sleep"
    last, timer = b.get("last"), b.get("timer")
    if not last:
        return "off" if timer and not timer.get("active") else "sleep"
    if last.get("signed_in") is False or last.get("outcome") in BAD:
        return "bad"
    if last.get("outcome") in WORKING:
        return "work"
    if timer and not timer.get("active"):
        return "off"
    if last.get("outcome") in DONE and (minutes_ago(last.get("ended")) or 0) < 60:
        return "done"
    return "sleep"


def state_word(b, here):
    last = b.get("last")
    if b.get("machine") != here:
        return f"on {b.get('machine')}"
    if b.get("rounds") == "none":
        return "sessions only"
    st = bot_state(b, here)
    if st == "off":
        return "paused"
    if not last:
        return "no round yet"
    if st in ("work", "bad"):
        return f"{last.get('outcome')} {last.get('what') or ''}".strip()
    if st == "done":
        return f"{last.get('outcome')} {ago(last.get('ended'))} ago"
    return f"idle {ago(last.get('ended'))}"


def lines(data, project=None):
    """The report as a list of lines."""
    out = []
    page = data.get("page") or {}
    here = (data.get("machine") or {}).get("name")
    name = f"{page['fleet']} fleet" if page.get("fleet") else "fleet"
    items = [i for i in data.get("items") or [] if project is None or i.get("project") == project]
    gen = data.get("generated")
    head = f"{name} · collected {ago(gen)} ago"
    if (minutes_ago(gen) or 0) > STALE_MIN:
        head += " · STALE: the collector is silent"
    if data.get("error"):
        head += f" · {data['error']}"
    out.append(head)
    for kind, title in (("merge", "Merge"), ("decide", "Decide"), ("step", "Step in")):
        its = [i for i in items if i.get("kind") == kind]
        if not its:
            continue
        out.append(f"{title} ({len(its)})")
        for i in its:
            who = i.get("bot") or i.get("label") or ""
            text = " ".join(x for x in (who, i.get("text")) if x)
            why = f"  ({i['why']})" if i.get("why") else ""
            age = f"  · {ago(i['since'])}" if i.get("since") and (minutes_ago(i["since"]) or 0) > 1440 else ""
            out.append(f"  {i.get('project', ''):<14} {i.get('tag', ''):<15} {text}{why}{age}")
    if not items:
        out.append("nothing waits on you" + (f" in {project}" if project else ""))
    projects = [p for p in data.get("projects") or [] if project is None or p.get("name") == project]
    bots = [b for b in data.get("bots") or [] if project is None or b.get("project") == project]
    L = {"ready": "ready", "next": "next", "decide": "needs-haku", "live": "live", **(page.get("labels") or {})}
    out.append("Projects")
    for p in projects:
        if p.get("error"):
            out.append(f"  {p['name']:<14} error: {p['error']}")
            continue
        ready = sum(1 for i in p.get("issues") or [] if L["ready"] in i.get("labels", []) and L["live"] not in i.get("labels", []))
        prs = sum(1 for pr in p.get("prs") or [] if not pr.get("draft"))
        mine = [b for b in bots if b.get("project") == p["name"] and b.get("rounds") != "none"]
        paused = " · paused" if mine and all(b.get("timer") and not b["timer"].get("active") for b in mine) else ""
        nxt = next((i for i in p.get("issues") or [] if L["next"] in i.get("labels", [])), None)
        out.append(f"  {p['name']:<14} {ready} ready · {prs} open pull request{'s' if prs != 1 else ''}"
                   f"{' · next #' + str(nxt['number']) if nxt else ''}{paused}")
    out.append("Bots")
    for b in bots:
        today = f"  today {b.get('rounds_today', 0)} runs ${b.get('cost_today') or 0:.2f}" if b.get("machine") == here and b.get("rounds") != "none" else ""
        timer = b.get("timer")
        t = f"  timer {'on' if timer.get('active') else 'off'}" if timer else ""
        out.append(f"  {b['name']:<18} {b.get('kind', ''):<5} {b.get('project', ''):<14} {state_word(b, here)}{today}{t}")
    m = data.get("machine") or {}
    if project is None and m:
        disks = ", ".join(f"{d} {v.get('free_gb')} GB free" for d, v in (m.get("disks") or {}).items())
        load = " ".join(str(x) for x in m.get("load") or [])
        gpu = m.get("gpu") or {}
        g = f" · GPU {gpu['util_pct']} %" if gpu.get("util_pct") is not None else ""
        g += f" · GPU lock: {gpu['lock']}" if gpu.get("lock") else ""
        out.append(f"Machine {m.get('name', '')}: up {m.get('uptime_h', '?')} h · load {load} · {disks}{g}")
        guards = []
        for x in data.get("guards") or []:
            if x.get("error"):
                guards.append(f"{x['host']} no report")
            else:
                crit = sum(1 for f in x.get("findings") or [] if f.startswith("CRIT"))
                n = len(x.get("findings") or [])
                guards.append(f"{x['host']} {'clean' if not n else f'{n} open' + (f', {crit} CRIT' if crit else '')} ({x.get('age_min', '?')} min)")
        if guards:
            out.append("Guards: " + " · ".join(guards))
    return out


def main(path, project=None, out=sys.stdout):
    try:
        data = json.loads(Path(path).read_text())
    except OSError as e:
        print(f"fleet status: {path}: {e.strerror or e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"fleet status: {path}: not JSON: {e}", file=sys.stderr)
        return 1
    if project is not None and not any(p.get("name") == project for p in data.get("projects") or []):
        names = ", ".join(p.get("name", "?") for p in data.get("projects") or [])
        print(f"fleet status: no project {project} in the JSON (there: {names})", file=sys.stderr)
        return 1
    for line in lines(data, project):
        print(line, file=out)
    return 2 if (minutes_ago(data.get("generated")) or 0) > STALE_MIN else 0
