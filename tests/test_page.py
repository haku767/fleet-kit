import pathlib
import subprocess
import sys
import tempfile
import unittest

from fleet_kit import model, page
from test_rounds import FakeSystemctl

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"


def load(**machine):
    _, doc = model.load(MINIMAL)
    doc["fleet"]["agents_dir"] = "/srv/agents"
    doc["machines"]["box"]["guard"] = "box-guard"
    doc["machines"]["box"].update(machine)
    return doc


class KitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.kit = self.root / "usr/local/lib/fleet/kit"

    def test_copy_launcher_source(self):
        acts = page.apply_kit(root=self.root, run=lambda cmd: "")
        for rel in ("fleet_kit/cli.py", "fleet_kit/sets/fantasy.py", "fleet_kit/data/bot-round", "fleet_kit/data/fleet-collect.service"):
            self.assertTrue((self.kit / rel).is_file(), rel)
            self.assertEqual((self.kit / rel).stat().st_mode & 0o777, 0o644, rel)
        self.assertFalse(list((self.kit / "fleet_kit").glob("*.pyc")))
        self.assertTrue(list((self.kit / "fleet_kit/__pycache__").glob("cli.*.pyc")))   # byte-compiled in place
        src = (self.kit / "SOURCE").read_text()
        self.assertIn("source=", src)
        self.assertIn("commit=-", src)   # the fake git said nothing
        launcher = self.root / "usr/local/bin/fleet"
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
        text = launcher.read_text()
        self.assertTrue(text.startswith("#!"))
        self.assertIn(" -I\n", text.splitlines()[0] + "\n")
        self.assertIn(f'sys.path.insert(0, "{page.KIT_DIR}")', text)
        self.assertTrue(any(w == "added" and x.endswith("/fleet_kit/page.py") for w, x in acts))
        # the copy is the kit: it runs
        r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(self.kit)!r}); "
                            "from fleet_kit.cli import main; sys.exit(main(['--version']))"], capture_output=True, text=True)
        self.assertIn("fleet-kit", r.stdout + r.stderr)
        self.assertTrue(page.kit_matches(page.Site(self.root)))

    def test_second_run_keeps_and_stale_goes(self):
        page.apply_kit(root=self.root, run=lambda cmd: "")
        acts = page.apply_kit(root=self.root, run=lambda cmd: "")
        self.assertEqual({w for w, _ in acts}, {"kept"})
        (self.kit / "fleet_kit/old.py").write_text("x = 1\n")
        self.assertFalse(page.kit_matches(page.Site(self.root)))
        acts = page.apply_kit(root=self.root, run=lambda cmd: "")
        self.assertIn(("removed", f"{page.KIT_DIR}/fleet_kit/old.py (the kit no longer has it)"), acts)
        self.assertFalse((self.kit / "fleet_kit/old.py").exists())
        self.assertTrue(page.kit_matches(page.Site(self.root)))

    def test_source_note_from_git(self):
        sha = "a" * 40
        note = page.source_note("/x", run=lambda cmd: sha + "\n" if cmd[-1] == "HEAD" else " M file\n")
        self.assertEqual(note, f"source=/x\ncommit={sha} (with local changes)\n")

    def test_dry_run_writes_nothing(self):
        acts = page.apply_kit(root=self.root, dry_run=True, run=lambda cmd: "")
        self.assertFalse(self.kit.exists())
        self.assertFalse((self.root / "usr/local/bin/fleet").exists())
        self.assertIn(("added", page.LAUNCHER), acts)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "srv/agents").mkdir(parents=True)
        self.sc = FakeSystemctl()
        self.pd = self.root / "pagedata"
        (self.pd / "sets").mkdir(parents=True)
        (self.pd / "index.html").write_text("<html>frame</html>\n")
        (self.pd / "sets/minimal.js").write_text("fleet.set('minimal', {})\n")
        self.fleet_dir = self.root / "srv/agents/fleet"

    def apply(self, doc, **kw):
        kw.setdefault("page_data", self.pd)
        return page.apply_page(doc, "box", "name = 'test'\n", root=self.root, systemctl=self.sc, **kw)

    def kit(self):
        page.apply_kit(root=self.root, run=lambda cmd: "")

    def test_refuses_without_the_kit(self):
        acts = self.apply(load())
        self.assertEqual(acts[0][0], "missing")
        self.assertIn("apply kit first", acts[0][1])
        self.assertFalse((self.root / "etc/fleet/fleet.toml").exists())

    def test_installs(self):
        self.kit()
        acts = self.apply(load(gpu=True))
        self.assertEqual((self.root / "etc/fleet/fleet.toml").read_text(), "name = 'test'\n")
        for d in ("", "/state"):
            p = self.root / f"srv/agents/fleet{d}"
            self.assertTrue(p.is_dir(), d)
            self.assertEqual(p.stat().st_mode & 0o7777, 0o2755, d)
        self.assertEqual((self.fleet_dir / "index.html").read_text(), "<html>frame</html>\n")
        self.assertTrue((self.fleet_dir / "sets/minimal.js").is_file())
        service = (self.root / "etc/systemd/system/fleet-collect.service").read_text()
        for line in ("User=box-guard", "Environment=HOME=/srv/box-guard", "ReadWritePaths=/srv/agents/fleet -/srv/box-guard/.cache",
                     "ExecStart=/usr/local/bin/fleet collect /etc/fleet/fleet.toml", "nvidia-smi"):
            self.assertIn(line, service)
        self.assertNotIn("PrivateDevices", service)
        self.assertNotIn("{", service)
        timer = (self.root / "etc/systemd/system/fleet-collect.timer").read_text()
        self.assertIn("OnUnitActiveSec=5m", timer)
        self.assertIn(["daemon-reload"], self.sc.calls)
        self.assertIn(["enable", "fleet-collect.timer"], self.sc.calls)
        self.assertIn(["start", "fleet-collect.timer"], self.sc.calls)
        self.assertNotIn(["start", "fleet-collect.service"], self.sc.calls)
        self.assertIn(("enabled", "fleet-collect.timer (running)"), acts)
        self.assertTrue(any(w == "kept" and "no run now" in x for w, x in acts))

    def test_no_gpu_tightens_and_every_from_the_file(self):
        self.kit()
        doc = load()
        doc["page"] = {"collect_every": "10m"}
        self.apply(doc)
        service = (self.root / "etc/systemd/system/fleet-collect.service").read_text()
        self.assertIn("PrivateDevices=yes", service)
        self.assertIn("ProtectProc=invisible", service)
        self.assertIn("OnUnitActiveSec=10m", (self.root / "etc/systemd/system/fleet-collect.timer").read_text())

    def test_second_run_keeps_interval_change_restarts_start_runs(self):
        self.kit()
        self.apply(load())
        acts = self.apply(load())
        self.assertEqual({w for w, _ in acts}, {"kept"})
        doc = load()
        doc["page"] = {"collect_every": "2m"}
        acts = self.apply(doc)
        self.assertIn(["restart", "fleet-collect.timer"], self.sc.calls)
        self.assertTrue(any(w == "changed" and "restarted" in x for w, x in acts))
        acts = self.apply(doc, start=True)
        self.assertIn(("started", "fleet-collect.service (one run now)"), acts)
        self.assertIn(["start", "fleet-collect.service"], self.sc.calls)

    def test_assets_mirrored_root_owned(self):
        self.kit()
        assets = self.root / "myassets"
        (assets / "sprites").mkdir(parents=True)
        (assets / "identities.json").write_text("{}\n")
        (assets / "sprites/a.png").write_bytes(b"\x89PNG")
        (assets / "old.txt").write_text("x\n")
        self.apply(load(), assets=assets)
        self.assertEqual((self.fleet_dir / "assets/sprites/a.png").read_bytes(), b"\x89PNG")
        self.assertEqual((self.fleet_dir / "assets").stat().st_mode & 0o777, 0o755)
        (assets / "old.txt").unlink()
        acts = self.apply(load(), assets=assets)
        self.assertIn(("removed", "/srv/agents/fleet/assets/old.txt (not in --assets any more)"), acts)
        self.assertFalse((self.fleet_dir / "assets/old.txt").exists())
        self.assertEqual(self.apply(load(), assets=self.root / "nowhere")[-1][0] != "missing", True)   # reported, the rest goes on
        self.assertTrue(any(w == "missing" and "not a directory" in x for w, x in self.apply(load(), assets=self.root / "nowhere")))

    def test_quest_json_goes_once_taken_over(self):
        self.kit()
        self.fleet_dir.mkdir(parents=True)
        (self.fleet_dir / "quest.json").write_text("{}\n")
        acts = self.apply(load())
        self.assertTrue(any(w == "kept" and "quest.json until" in x for w, x in acts))
        self.assertTrue((self.fleet_dir / "quest.json").exists())
        for s in ("items", "fantasy"):
            (self.fleet_dir / f"state/{s}.json").write_text("{}\n")
        acts = self.apply(load())
        self.assertIn(("removed", "/srv/agents/fleet/quest.json (taken over into state/)"), acts)
        self.assertFalse((self.fleet_dir / "quest.json").exists())

    def test_no_page_files_yet_is_said(self):
        self.kit()
        acts = self.apply(load(), page_data=self.root / "nothing")
        self.assertTrue(any(w == "skipped" and "no page files yet" in x for w, x in acts))
        self.assertTrue((self.root / "etc/systemd/system/fleet-collect.timer").is_file())   # the collector runs anyway

    def test_dry_run_and_other_machines(self):
        self.kit()
        acts = self.apply(load(), dry_run=True)
        self.assertFalse((self.root / "etc/fleet/fleet.toml").exists())
        self.assertEqual({c[0] for c in self.sc.calls}, {"show"})
        self.assertIn(("added", "/etc/fleet/fleet.toml"), acts)
        doc = load(family="nixos")
        self.assertEqual(self.apply(doc)[0][0], "skipped")
        self.assertEqual(page.apply_page(load(), "elsewhere", "", root=self.root, systemctl=self.sc)[0][0], "skipped")
        doc = load()
        del doc["machines"]["box"]["guard"]
        self.assertEqual(self.apply(doc)[0][0], "missing")   # no guard: nobody to run as


TS_STATUS = """https://box.example.ts.net (tailnet only)
|-- /                 proxy http://127.0.0.1:8200
|-- /fleet            path  /srv/agents/fleet/index.html
|-- /fleet/assets/    path  /srv/agents/fleet/assets
|-- /fleet/fleet.json path  /srv/agents/fleet/fleet.json

https://box.example.ts.net:8443 (tailnet only)
|-- / proxy http://127.0.0.1:2586
"""


class ServeHintTests(unittest.TestCase):
    def test_tailscale_old_entries_and_the_directory(self):
        lines = page.serve_hints({"page": {"serve": "tailscale", "path": "/fleet"}}, "/srv/agents", TS_STATUS)
        self.assertIn("  sudo tailscale serve --bg --set-path /fleet off   # an older entry under /fleet", lines)
        self.assertIn("  sudo tailscale serve --bg --set-path /fleet/assets off   # an older entry under /fleet", lines)
        self.assertIn("  sudo tailscale serve --bg --set-path /fleet/fleet.json off   # an older entry under /fleet", lines)
        self.assertTrue(any(l.startswith("  sudo tailscale serve --bg --set-path /fleet /srv/agents/fleet") for l in lines))
        self.assertFalse(any("/ off" in l for l in lines))   # the root proxy is not ours

    def test_tailscale_in_place(self):
        status = "https://box.example.ts.net (tailnet only)\n|-- /fleet/ path /srv/agents/fleet\n"
        lines = page.serve_hints({"page": {"serve": "tailscale", "path": "/fleet"}}, "/srv/agents", status)
        self.assertIn("in place", lines[0])
        self.assertFalse(any("--set-path /fleet /srv" in l for l in lines))

    def test_caddy_nginx_none(self):
        self.assertIn("  handle_path /fleet/* {", page.serve_hints({"page": {"serve": "caddy"}}, "/srv/agents"))
        self.assertIn("  location /p/ { alias /srv/agents/fleet/; }", page.serve_hints({"page": {"serve": "nginx", "path": "/p/"}}, "/srv/agents"))
        self.assertTrue(any("http.server" in l for l in page.serve_hints({}, "/srv/agents")))


if __name__ == "__main__":
    unittest.main()
