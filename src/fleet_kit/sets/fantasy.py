"""The fantasy set's collector half: the game's numbers, computed once so every browser agrees.

- quest: the day's quest. The most items open today is the quest's size, what has gone since is
  done; a day the list reached zero counts for the streak; the done list says what went today
  and when (the owner did it, or it resolved itself). An item that comes back leaves the done
  list; items of a project the collector cannot read right now are not done (a GitHub hiccup is
  no feat). State: day, max, cleared (the last 90 days that reached zero), done, days.
- levels: XP, level and badges per project and per bot from the shared history. A project earns
  10 XP per merged pull request, 3 per closed issue, 2 per done round of its bots; a bot 10 per
  merged pull request of its own and 2 per done round. Level = floor(sqrt(XP / 20)). Badges
  rank up, wood to diamond, one badge per thing; the tooltip text (`why`) carries the value and
  the next rank's threshold.
- fleet_streak: days in a row with a merge anywhere.
- budgets: the mana bars' defaults from `[page.fantasy]` (budget_dev_usd, budget_plan_usd).
"""

import math
from datetime import date, datetime

XP = {"merged": 10, "closed": 3, "done": 2}
RANKS = ["wood", "bronze", "silver", "gold", "platinum", "diamond"]
DAYS = [1, 3, 7, 14, 21, 30]


def local_day(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (ValueError, AttributeError, TypeError):
        return None


def level(xp):
    return math.floor(math.sqrt(max(0, xp) / 20))


def day_word(n):
    return f"{n} day{'' if n == 1 else 's'}"


def money(x):
    return f"${x:.2f}"


def days_in_row(arr, has):
    """Days in a row ending today, or yesterday when today has nothing yet."""
    n, i = 0, len(arr) - 1
    if i >= 0 and not has(arr[i]):
        i -= 1
    while i >= 0 and has(arr[i]):
        n += 1
        i -= 1
    return n


def rank(name, value, steps, fmt=str, lower=False):
    ok = (lambda s: value <= s) if lower else (lambda s: value >= s)
    r = -1
    while r + 1 < len(steps) and ok(steps[r + 1]):
        r += 1
    nxt = f"{RANKS[r + 1]} at {fmt(steps[r + 1])}" if r + 1 < len(steps) else "top rank"
    return {"name": name, "r": r, "why": f"{fmt(value)}; {nxt}"}


def sum_bots(hists, key):
    """The bots' per-day lists added up; None when no bot has one (a bot without a log has none)."""
    rs = [h[key] for h in hists if h and h.get(key)]
    if not rs:
        return None
    return [sum(r[i] for r in rs if i < len(r)) for i in range(len(rs[0]))]


def clean_steady(rounds, bad):
    if not rounds:
        return []
    bad = bad or [0] * len(rounds)
    return [rank("clean", days_in_row([r > 0 and not bad[i] for i, r in enumerate(rounds)], bool), DAYS, day_word),
            rank("steady", days_in_row(rounds, lambda r: r > 0), DAYS, day_word)]


def who(author, owner):
    a = author or ""
    return a[4:].removeprefix(f"{owner}-") if a.startswith("app/") else a


def bot_merged(h, ph, bot, owner):
    """A bot's merged pull requests: all time from the search when the collector has it, else the window."""
    if h.get("merged_total") is not None:
        return h["merged_total"]
    for a, per in (ph.get("merged_by") or {}).items():
        if who(a, owner) == bot["name"]:
            return sum(per)
    return 0


def levels(shared, owner):
    hist = shared.get("history") or {}
    hp, hb = hist.get("projects") or {}, hist.get("bots") or {}
    out = {"projects": {}, "bots": {}}
    for p in shared["projects"]:
        h = hp.get(p["name"])
        if not h or h.get("merged") is None:
            continue
        bots = [b for b in shared["bots"] if b["project"] == p["name"]]
        hs = [hb.get(b["name"]) for b in bots]
        merged = h.get("merged_total") if h.get("merged_total") is not None else sum(h["merged"])
        closed = h.get("closed_total") if h.get("closed_total") is not None else sum(h.get("closed") or [])
        done = sum((x or {}).get("done_total") or 0 for x in hs)
        bad = sum((x or {}).get("bad_total") or 0 for x in hs)
        xp = merged * XP["merged"] + closed * XP["closed"] + done * XP["done"]
        m = h.get("merged") or []
        badges = [rank("merged", merged, [1, 10, 50, 100, 250, 500]),
                  rank("streak", days_in_row(m, lambda v: v > 0), [2, 3, 5, 7, 14, 30], day_word),
                  rank("big day", max([0, *m]), [2, 3, 5, 8, 12, 20], lambda v: f"{v} in a day"),
                  *clean_steady(sum_bots(hs, "rounds"), sum_bots(hs, "bad"))]
        out["projects"][p["name"]] = {"xp": xp, "lv": level(xp), "merged": merged, "closed": closed, "done": done, "bad": bad, "badges": badges}
    for b in shared["bots"]:
        h = hb.get(b["name"])
        if not h:
            continue
        merged = bot_merged(h, hp.get(b["project"]) or {}, b, owner)
        done = h.get("done_total") or 0
        xp = merged * XP["merged"] + done * XP["done"]
        r30, c30 = sum(h.get("rounds") or []), sum(h.get("cost") or [])
        frugal = c30 / r30 if r30 >= 20 else None
        badges = [rank("done", done, [5, 10, 25, 50, 100, 250], lambda v: f"{v} done round{'' if v == 1 else 's'} (built, fixed or reviewed)"),
                  rank("merged", merged, [1, 10, 50, 100, 250, 500]),
                  *clean_steady(h.get("rounds"), h.get("bad")),
                  rank("frugal", frugal if frugal is not None else math.inf, [0.5, 0.25, 0.1, 0.05, 0.02, 0.01],
                       lambda v: "fewer than 20 rounds in 30 days" if v == math.inf else f"{money(v)} per round, 30 days", lower=True)]
        out["bots"][b["name"]] = {"xp": xp, "lv": level(xp), "merged": merged, "done": done, "badges": badges}
    return out


def fleet_streak(shared):
    hist = shared.get("history") or {}
    days = hist.get("days") or []
    per = [sum((p.get("merged") or [0] * len(days))[i] if i < len(p.get("merged") or []) else 0
               for p in (hist.get("projects") or {}).values()) for i in range(len(days))]
    n = days_in_row(per, lambda v: v > 0)
    today_has = bool(per) and per[-1] > 0
    return {"n": n, "today_has": today_has, "per": per, "capped": bool(n) and n >= len(per) - 1}


def quest(items, ctx, st):
    today, now, days = ctx["today"], ctx["now"], ctx["days"]
    open_n = len(items)
    if st.get("day") != today:
        st["day"], st["max"] = today, 0
    st["max"] = max(st.get("max", 0), open_n)
    cleared = sorted(set(st.get("cleared", [])))[-90:]
    if open_n == 0 and today not in cleared:
        cleared.append(today)
    st["cleared"] = cleared
    ids = {i["id"] for i in items}
    unreadable = {i["project"] for i in items if i["id"].startswith("step:error:")}
    was = {i["id"]: i for i in (ctx.get("previous") or {}).get("items") or []}
    done = {k: v for k, v in (st.get("done") or {}).items() if local_day(v.get("at")) == today and k not in ids}
    for k, p in was.items():
        if k in ids or p.get("project") in unreadable or k.startswith("step:error:"):
            continue
        done[k] = {"at": now, "since": p.get("since"), **{f: p.get(f) for f in ("kind", "tag", "cls", "project", "text", "label", "url", "bot")}}
    st["done"] = done
    st.pop("seen", None)   # the shared part keeps `since` now (state/items.json)
    per_day = st.get("days", {})
    per_day[today] = {"max": st["max"], "last": open_n}
    st["days"] = {d: v for d, v in per_day.items() if d >= days[0]}
    # the streak: days in a row that reached zero, ending today or (today still open) yesterday
    cs, streak, d = set(cleared), 0, date.fromisoformat(today)
    if today not in cs:
        d = date.fromordinal(d.toordinal() - 1)
    while d.isoformat() in cs:
        streak += 1
        d = date.fromordinal(d.toordinal() - 1)
    return {"day": today, "open": open_n, "max": st["max"], "streak": streak, "cleared_today": today in cs,
            "days": [dict(st["days"].get(x, {"max": None, "last": None}), day=x) for x in days],
            "done": sorted((dict(v, id=k) for k, v in done.items()), key=lambda x: x["at"], reverse=True)}, st


def collect(shared, state, ctx):
    q, state = quest(shared["items"], ctx, dict(state))
    knobs = ctx.get("knobs") or {}
    block = {"quest": q, "levels": levels(shared, ctx["owner"]), "fleet_streak": fleet_streak(shared),
             "budgets": {"dev": float(knobs.get("budget_dev_usd", 2.0)), "plan": float(knobs.get("budget_plan_usd", 1.0))},
             "xp": XP, "ranks": RANKS}
    return block, state
