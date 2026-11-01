# Writing a set: a look of the page with its own logic

The page draws one JSON, `fleet.json`, that `fleet collect` writes every few minutes. How the
overview looks is a *set*: the kit ships `default` (the plain desk), `minimal` (the three lists
and nothing else) and `fantasy` (the fleet as a game). A set of your own is two files next to
the page, under `assets/sets/`, with one name:

| File | Runs | Does |
|---|---|---|
| `assets/sets/<name>.js` | in the browser | draws the overview (and, if it wants, the project, bot and machine pages) from the JSON |
| `assets/sets/<name>.py` | inside `fleet collect`, after the shared part | computes what every browser must agree on and keeps state across runs; optional |

A set may have only the `.js`. `fleet apply page --assets DIR` copies the directory next to the
page, root-owned; `[page] set = "<name>"` in `fleet.toml` opens the page with it, and every
viewer can switch in the line under the view (`look: default minimal fantasy mine`). The sets
offered there are `[page] sets`, or the kit's three plus every own one found.

## The page half

```js
// assets/sets/mine.js: the three lists, then one line per project
(() => {
  const F = window.fleet;                        // the frame's helpers
  const {esc, tag, todo, actions, queued, plink} = F;
  F.set("mine", {
    title: "mine",
    css: `.proj { font-size: 15px; }`,          // optional, applied while this set is shown
    overview(d) {                                // d is the whole JSON
      const items = actions(d);                  // the items, with their html, plus "collector silent"
      const my = (d.sets || {}).mine || {};      // this set's block from the collector half, if any
      return `${todo(items) || `<div class="card"><h3>Nothing waits on you</h3></div>`}
        <h2>Projects</h2>
        ${d.projects.map(p => `<div class="card proj"><a href="${plink(p.name)}">${esc(p.name)}</a>
          ${tag(queued(p).length + " ready")}${my.streak ? tag(my.streak[p.name] + " days", "green") : ""}</div>`).join("")}`;
    },
    // project(d, p), bot(d, b), machine(d): optional; without them the frame's plain pages show
    // after(root, d): optional, runs after a draw, for click handlers
    // pinned(d): optional, project names the side menu lists first
  });
})();
```

`window.fleet` holds what the frame knows: `esc`, `tag`, `ago`, `when`, `money`, `who` (an
App's login to the bot's name), `plink`/`blink` (links to a project's or bot's page), `store`
(this browser's memory, for a favourite or a setting), `actions`/`todo` (the three lists),
`itemHtml`, `queued`, `devPRs`, `reviewer`, `hist`/`hp`/`hb` (the history and a project's or
bot's entry), `spark` (a row of day bars), `botState`/`stateWord`/`figure`/`botFigure`/`botLine`,
`pipelineBar`, `projectChips`, `slimLine`, `prsTable`, `issuesTable`, `botCard`, `hero`,
`roundsTable`, `guard`, `machine`, `guardsLine`, `labels()` (which label means ready, next,
decide, live in this fleet), `page()` (the JSON's `page` block), `nameOf` (a bot's persona),
`plain` (the frame's pages), `render()` and `data`. The CSS variables are `--bg`, `--fg`, `--dim`,
`--line`, `--card`, `--green`, `--red`, `--amber`, `--blue`, `--purple`; use them and the look
works in dark and light. The kit's `minimal.js` is the shortest example, `fantasy.js` the
longest; both are in `src/fleet_kit/data/page/sets/`.

A page half draws; it does not fetch. What has to be the same in every browser (a streak, a
score, a day's maximum) belongs in the collector half.

## The collector half

```python
# assets/sets/mine.py: days in a row with a merge, per project
def collect(shared, state, ctx):
    hist = shared["history"]
    streak = {}
    for name, h in hist["projects"].items():
        n = 0
        for v in reversed(h.get("merged") or []):
            if not v:
                break
            n += 1
        streak[name] = n
    best = {k: max(v, state.get("best", {}).get(k, 0)) for k, v in streak.items()}
    return {"streak": streak, "best": best}, {"best": best}
```

`collect(shared, state, ctx)` returns `(block, state)`, two dicts that JSON can hold. `shared`
is the JSON so far: `projects`, `bots`, `guards`, `machine`, `history` (30 days), `items` with
`since`; read it, do not change it. `state` is what the last run returned as state, from
`state/<name>.json` next to the JSON (`{}` the first time). `ctx` carries `now` (ISO, UTC),
`today` and `days` (local dates, today last), `owner`, `fleet`, `machine`, `knobs` (the file's
`[page.<name>]` table, for a budget or a threshold) and `previous` (the previous JSON, or
`None`). The block lands under `sets.<name>` in the JSON.

An exception, or a run longer than 30 seconds, is recorded under `sets.<name>.error` and stops
neither the run nor the other sets; the page half sees the error in its block and should say
so. A half that hangs keeps its thread until the collector's process ends, which a oneshot
unit does; do not loop forever. The half runs as the collector's user inside the unit's sandbox:
the system read-only, only the page directory writable, no home, no devices beyond the GPU
when the machine has one, the service syscalls. It may read what the collector reads and
nothing else; it does not need to, the JSON has the facts.

## Trying it

Locally, without the machine: a directory with the kit's `index.html` and `sets/` (from
`src/fleet_kit/data/page/`), your `assets/sets/`, and a `fleet.json`:

    fleet collect fleet.toml --out /path/to/trial/fleet.json
    cd /path/to/trial && python3 -m http.server 8000

and `http://localhost:8000/#` in a browser; `--out` away from the live path reads neither the
live JSON nor the live state, so copy both in when the quest or a streak should carry over. A
headless browser shows what a viewer gets: `google-chrome --headless=new --dump-dom
http://localhost:8000/` prints the drawn page, `--screenshot=page.png` a picture. The collector
half alone: `python3 -c 'import json, mine; print(mine.collect(json.load(open("fleet.json")), {}, {"owner": "me", "knobs": {}}))'`.

On the machine: `fleet apply page --assets DIR` again after a change (it copies over), then
reload the page; the collector half is in play at the next run of `fleet-collect.timer`.
