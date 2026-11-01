"""The sets' collector halves.

A set is a look of the page with its own logic, in two halves with one name: `sets/<name>.js`
draws it, and an optional collector half computes what every browser must agree on, run inside
`fleet collect` after the shared part. The kit's sets are `default` and `minimal` (page halves
only) and `fantasy` (this package's `fantasy.py`); an own set is `assets/sets/<name>.py` and
`.js` next to the page.

A collector half is a module with

    def collect(shared, state, ctx) -> (block, state)

`shared` is the JSON so far (projects, bots, guards, machine, history, items with `since`; read
it, do not change it), `state` the set's previous state from `state/<name>.json` next to the
JSON (`{}` the first time), `ctx` a dict: `now` (ISO, UTC), `today` and `days` (the history's
local dates, today last), `owner`, `fleet`, `machine`, `knobs` (the file's `[page.<name>]`
table) and `previous` (the previous JSON, or None). The block lands under `sets.<name>`, the
state is written back. An exception or a run longer than TIMEOUT seconds is recorded under
`sets.<name>.error` and stops neither the run nor the other sets.
"""

import importlib
import importlib.util
import json
import sys
import threading
import traceback
from pathlib import Path

BUILTIN = ["default", "minimal", "fantasy"]
TIMEOUT = 30


def names(wanted, assets):
    """The sets to prepare: the file's list, or the kit's plus every own one found in
    assets/sets (a `.py` or a `.js`), in that order."""
    if wanted:
        return list(wanted)
    own = sorted({p.stem for p in Path(assets, "sets").glob("*.py")} | {p.stem for p in Path(assets, "sets").glob("*.js")}
                 if Path(assets, "sets").is_dir() else set())
    return BUILTIN + [n for n in own if n not in BUILTIN]


def load(name, assets):
    """(module or None, error or None): the kit's module, else assets/sets/<name>.py; None without
    an error for a set that has only a page half (a kit set, or an own `.js`)."""
    if not name.replace("-", "_").replace("_", "").isalnum():
        return None, "not a set name"
    own = Path(assets, "sets", f"{name}.py")
    if own.is_file():
        try:
            spec = importlib.util.spec_from_file_location(f"fleet_set_{name}", own)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, None
        except Exception as e:   # the owner's code: whatever it raises is its error
            return None, f"{own}: {e.__class__.__name__}: {e}"
    if name in BUILTIN:
        try:
            return importlib.import_module(f"fleet_kit.sets.{name}"), None
        except ModuleNotFoundError:
            return None, None
    if Path(assets, "sets", f"{name}.js").is_file():
        return None, None
    return None, f"no such set: neither the kit's nor {own.with_suffix('')}.py or .js"


def run_one(mod, shared, state, ctx):
    """collect() on a thread with a timeout; (block, state, error)."""
    result = {}

    def go():
        try:
            result["value"] = mod.collect(shared, state, ctx)
        except Exception:
            result["error"] = traceback.format_exc(limit=3).strip().splitlines()[-1]
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(TIMEOUT)
    if t.is_alive():
        return None, None, f"no answer after {TIMEOUT} s"
    if "error" in result:
        return None, None, result["error"]
    v = result.get("value")
    if not (isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], dict) and isinstance(v[1], dict)):
        return None, None, "collect() must return (block, state), two dicts"
    return v[0], v[1], None


def run_all(names_, shared, previous, cfg):
    """Every set's block, by name; the state files under cfg.state_dir."""
    from .. import collect as c   # the JSON helpers; collect imports this package
    out = {}
    base = {"now": shared["generated"], "today": shared["history"]["days"][-1], "days": shared["history"]["days"],
            "owner": cfg.owner, "fleet": cfg.fleet, "machine": cfg.machine, "previous": previous}
    for name in names_:
        mod, err = load(name, cfg.assets)
        if mod is None:
            if err:
                out[name] = {"error": err}
            continue
        f = cfg.state_dir / f"{name}.json"
        state = c.read_json(f)
        if state is None and name == "fantasy":   # the first run after the move: the old quest file
            state = c.read_json(cfg.out.with_name("quest.json"))
        if not isinstance(state, dict):
            state = {}
        ctx = dict(base, knobs=dict(cfg.knobs.get(name) or {}))
        block, state, err = run_one(mod, shared, state, ctx)
        if err:
            out[name] = {"error": err}
            continue
        try:
            json.dumps(block)
            c.write_json(f, state)
        except (TypeError, ValueError) as e:
            out[name] = {"error": f"block not JSON: {e}"}
            continue
        except OSError as e:
            print(f"{name}: state not written: {e}", file=sys.stderr)
        out[name] = block
    return out
