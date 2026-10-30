import json
import os
import pathlib
import shutil
import tempfile
import unittest

from fleet_kit import machine, model, render

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"
NOW = 1_800_000_000.0


class World:
    """A temporary root laid out like a bots machine for examples/minimal.toml: machine `box`,
    accounts myapp-plan and myapp-dev, both timers on, one round each behind them."""

    def __init__(self, doc, paused=False):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.doc = doc
        if paused:
            doc["projects"]["myapp"]["paused"] = True
        self.owners = {}          # path -> owner name; everything else is root's
        self.users = {"myapp-plan": "/srv/myapp-plan", "myapp-dev": "/srv/myapp-dev"}
        self.units = {"bot-round@myapp-dev.timer": ("enabled", "active"),
                      "bot-review@myapp-plan.timer": ("enabled", "active")}
        self.calls = []
        self.build()

    def p(self, path):
        return self.root / path.lstrip("/")

    def mk(self, path, text=None, mode=None, owner=None):
        q = self.p(path)
        if text is None:
            q.mkdir(parents=True, exist_ok=True)
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text(text)
        if mode is not None:
            os.chmod(q, mode)
        if owner:
            self.owners[str(q)] = owner
        return q

    def build(self):
        files = render.render(self.doc)
        for acct, home in self.users.items():
            self.mk(home, mode=0o750, owner=acct)
            os.symlink("/srv/agents", self.p(home) / "agents")
            self.mk(f"/var/lib/systemd/linger/{acct}", "")
            self.mk(f"{home}/.local/bin/claude", "#!/bin/sh\n", 0o755, acct)
            self.mk(f"{home}/.config/fleet/app-id", "1\n", 0o600, acct)
            self.mk(f"{home}/.config/fleet/app.pem", "key\n", 0o600, acct)
            self.mk(f"/etc/fleet/bots/{acct}.conf", files[f"bots/{acct}.conf"], 0o644)
        self.mk("/etc/fleet/bots", mode=0o755)
        self.mk("/etc/fleet", mode=0o755)
        self.mk("/data/myapp-dev", mode=0o750, owner="myapp-dev")
        self.mk("/etc/fleet/lists.sh", files["machines/box/lists.sh"], 0o644)
        for s in machine.SCRIPTS:
            self.mk(s, "#!/bin/sh\n", 0o755)
        for u in machine.UNITS:
            self.mk(u, "[Unit]\n", 0o644)
        self.mk(machine.DEFAULTS_FILE, "BOT_ROUND_MAX=3\n", 0o644)
        self.mk(machine.RULES, "# rules\n", 0o644)
        self.mk("/srv/agents/LOG.md", "", 0o664)
        self.mk("/srv/agents/status", mode=0o2775)
        for name in ("myapp-plan", "myapp-dev"):
            self.mk(f"/srv/agents/inbox/{name}/done", owner=name)
            self.owners[str(self.p(f"/srv/agents/inbox/{name}"))] = name
        self.status("myapp-dev", "round", "idle", NOW - 600)
        self.status("myapp-plan", "review", "idle", NOW - 600)

    def status(self, name, kind, outcome, when, signed_in=True):
        q = self.mk(f"/srv/agents/status/{name}-{kind}.json",
                    json.dumps({"bot": name, "kind": kind, "outcome": outcome, "what": "x", "signed_in": signed_in}))
        os.utime(q, (when, when))

    def systemctl(self, args):
        self.calls.append(args)
        unit = args[1]
        st = self.units.get(unit, ("", "inactive"))
        return f"UnitFileState={st[0]}\nActiveState={st[1]}\n"

    def run(self):
        return machine.run(self.doc, "box", root=self.root, systemctl=self.systemctl,
                           user_of=lambda n: self.users.get(n),
                           owner_of=lambda p: self.owners.get(p, "root"), now=NOW)


def load():
    _, doc = model.load(MINIMAL)
    doc.setdefault("fleet", {})["agents_dir"] = "/srv/agents"
    return doc


def by_level(findings, level):
    return [str(x) for x in findings if x.level == level]


def where(findings, w):
    return [x for x in findings if x.where == w]


class MachineTests(unittest.TestCase):
    def test_green_world_has_no_warn_or_fail(self):
        w = World(load())
        f = w.run()
        self.assertEqual(by_level(f, "fail"), [])
        self.assertEqual(by_level(f, "warn"), [])
        self.assertEqual(by_level(f, "skip"), [])
        wheres = {x.where for x in f}
        for k in ("machine", "account:myapp-dev", "scratch:myapp-dev", "conf:myapp-plan", "claude:myapp-dev",
                  "token:myapp-plan", "timer:myapp-dev", "status:myapp-plan", "inbox:myapp-dev", "lists",
                  "scripts", "agents", "rules"):
            self.assertIn(k, wheres)
        self.assertNotIn("scratch:myapp-plan", wheres)   # a plan bot builds nothing

    def test_other_machine_is_skipped(self):
        w = World(load())
        f = machine.run(w.doc, "elsewhere", root=w.root)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].level, "skip")

    def test_missing_account_fails_and_stops_that_bot(self):
        w = World(load())
        del w.users["myapp-dev"]
        f = w.run()
        self.assertTrue(any("no Unix account myapp-dev" in x for x in by_level(f, "fail")))
        self.assertEqual(where(f, "conf:myapp-dev"), [])
        self.assertTrue(where(f, "conf:myapp-plan"))

    def test_conf_missing_wrong_owner_stale(self):
        w = World(load())
        os.remove(w.p("/etc/fleet/bots/myapp-dev.conf"))
        self.assertEqual(where(w.run(), "conf:myapp-dev")[0].level, "fail")
        w.mk("/etc/fleet/bots/myapp-dev.conf", "kind='dev'\n", 0o664)   # group-writable
        self.assertEqual(where(w.run(), "conf:myapp-dev")[0].level, "fail")
        w.mk("/etc/fleet/bots/myapp-dev.conf", "kind='dev'\nrepo='x'\n", 0o644)   # root's but stale
        x = where(w.run(), "conf:myapp-dev")[0]
        self.assertEqual(x.level, "warn")
        self.assertIn("fleet apply conf", x.message)
        w.mk("/etc/fleet/bots/myapp-dev.conf", "kind='plan'\n", 0o644)
        self.assertIn("another kind", where(w.run(), "conf:myapp-dev")[0].message)

    def test_lists_and_scripts(self):
        w = World(load())
        os.remove(w.p("/etc/fleet/lists.sh"))
        self.assertEqual(where(w.run(), "lists")[0].level, "fail")
        w.mk("/etc/fleet/lists.sh", "bots=''\n", 0o644)
        self.assertEqual(where(w.run(), "lists")[0].level, "warn")
        os.remove(w.p(machine.UNITS[1]))
        f = where(w.run(), "scripts")
        self.assertTrue(any(x.level == "fail" and "bot-round@.timer" in x.message for x in f))
        w.doc["machines"]["box"]["family"] = "nixos"
        f = w.run()
        self.assertEqual(where(f, "lists")[0].level, "skip")
        self.assertEqual(where(f, "scripts")[0].level, "skip")

    def test_timer_and_paused(self):
        w = World(load())
        w.units["bot-round@myapp-dev.timer"] = ("enabled", "inactive")
        x = where(w.run(), "timer:myapp-dev")[0]
        self.assertEqual(x.level, "fail")
        self.assertIn("enabled/inactive", x.message)
        p = World(load(), paused=True)
        f = p.run()
        self.assertEqual(where(f, "timer:myapp-dev")[0].level, "warn")   # paused, timer still on
        self.assertEqual(where(f, "status:myapp-dev")[0].level, "ok")
        p.units["bot-round@myapp-dev.timer"] = ("disabled", "inactive")
        self.assertEqual(where(p.run(), "timer:myapp-dev")[0].level, "ok")

    def test_status_signed_out_old_retry(self):
        w = World(load())
        w.status("myapp-dev", "round", "idle", NOW - 60, signed_in=False)
        self.assertEqual(where(w.run(), "status:myapp-dev")[0].level, "fail")
        w.status("myapp-dev", "round", "idle", NOW - 6 * 3600)
        x = where(w.run(), "status:myapp-dev")[0]
        self.assertEqual(x.level, "warn")
        self.assertIn("h ago", x.message)
        w.status("myapp-dev", "round", "retry", NOW - 60)
        self.assertEqual(where(w.run(), "status:myapp-dev")[0].level, "warn")
        os.remove(w.p("/srv/agents/status/myapp-plan-review.json"))
        self.assertIn("no round has run", where(w.run(), "status:myapp-plan")[0].message)

    def test_inbox_missing_owner_old_note(self):
        w = World(load())
        self.assertEqual(where(w.run(), "inbox:myapp-plan")[0].message, "empty")
        q = w.mk("/srv/agents/inbox/myapp-plan/2026-01-01-x.md", "hi\n")
        self.assertIn("1 note(s) waiting", where(w.run(), "inbox:myapp-plan")[0].message)
        os.utime(q, (NOW - 5 * 86400, NOW - 5 * 86400))
        x = where(w.run(), "inbox:myapp-plan")[0]
        self.assertEqual(x.level, "warn")
        self.assertIn("5 days", x.message)
        w.owners[str(w.p("/srv/agents/inbox/myapp-dev"))] = "somebody"
        self.assertIn("owned by somebody", where(w.run(), "inbox:myapp-dev")[0].message)
        # a bot whose account differs from its name: the account owns the inbox
        w.doc["projects"]["myapp"]["dev"]["account"] = "dev-acct"
        w.users["dev-acct"] = "/srv/myapp-dev"
        w.owners[str(w.p("/srv/agents/inbox/myapp-dev"))] = "dev-acct"
        self.assertEqual(where(w.run(), "inbox:myapp-dev")[0].level, "ok")
        shutil.rmtree(w.p("/srv/agents/inbox/myapp-dev"))
        self.assertEqual(where(w.run(), "inbox:myapp-dev")[0].level, "fail")

    def test_home_details_and_tokens(self):
        w = World(load())
        os.remove(w.p("/var/lib/systemd/linger/myapp-dev"))
        os.chmod(w.p("/srv/myapp-dev"), 0o755)
        x = where(w.run(), "account:myapp-dev")[0]
        self.assertEqual(x.level, "warn")
        self.assertIn("linger off", x.message)
        self.assertIn("mode 755", x.message)
        os.remove(w.p("/srv/myapp-plan/.config/fleet/app.pem"))
        self.assertEqual(where(w.run(), "token:myapp-plan")[0].level, "fail")
        os.remove(w.p("/srv/myapp-plan/.local/bin/claude"))
        self.assertEqual(where(w.run(), "claude:myapp-plan")[0].level, "fail")
        shutil.rmtree(w.p("/data/myapp-dev"))
        self.assertEqual(where(w.run(), "scratch:myapp-dev")[0].level, "fail")

    def test_agents_dir_and_guard_report(self):
        w = World(load())
        w.doc["machines"]["box"]["guard"] = "g"
        w.doc["guards"] = {"g": {"machine": "box", "apps": ["x"]}}
        x = where(w.run(), "agents")[0]
        self.assertEqual(x.level, "warn")
        self.assertIn("latest.txt is missing", x.message)
        q = w.mk("/srv/agents/reports/box/latest.txt", "ok\n")
        os.utime(q, (NOW - 600, NOW - 600))
        self.assertEqual(where(w.run(), "agents")[0].level, "ok")
        os.utime(q, (NOW - 4 * 3600, NOW - 4 * 3600))
        self.assertIn("h old", where(w.run(), "agents")[0].message)
        # a guard with an inbox here
        self.assertTrue(where(w.run(), "inbox:g"))
        os.chmod(w.p("/srv/agents/LOG.md"), 0o644)
        self.assertIn("not group-writable", where(w.run(), "agents")[0].message)

    def test_seconds(self):
        self.assertEqual(machine.seconds("20m"), 1200)
        self.assertEqual(machine.seconds("1h"), 3600)
        self.assertEqual(machine.seconds("90s"), 90)
        self.assertEqual(machine.seconds(None), 1200)
        self.assertEqual(machine.seconds("soon"), 1200)


if __name__ == "__main__":
    unittest.main()
