import importlib.resources
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

from fleet_kit import sets

PAGE = importlib.resources.files("fleet_kit") / "data" / "page"


class PageTests(unittest.TestCase):
    def test_frame_and_sets_are_shipped(self):
        html = (PAGE / "index.html").read_text()
        self.assertIn("<!doctype html>", html)
        self.assertIn("window.fleet = fleet", html)
        self.assertIn("fleet.json", html)
        for name in sets.BUILTIN:
            js = (PAGE / "sets" / f"{name}.js").read_text()
            self.assertRegex(js, rf'F\.set\("{name}",', f"{name}.js must register itself under its own name")
            self.assertIn("overview(d)", js)
        shipped = sorted(p.name[:-3] for p in (PAGE / "sets").iterdir() if p.name.endswith(".js"))
        self.assertEqual(shipped, sorted(sets.BUILTIN))

    def test_frame_knows_no_fleet(self):
        """The frame and the kit's sets are every fleet's: no owner, machine or bot name in them."""
        texts = [(PAGE / "index.html").read_text()] + [(PAGE / "sets" / f"{n}.js").read_text() for n in sets.BUILTIN]
        for t in texts:
            self.assertNotRegex(t, r'"(ws-msi|tx13)"')
            self.assertNotIn("haku767", t)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_scripts_parse(self):
        html = (PAGE / "index.html").read_text()
        js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in [("frame", js)] + [(n, (PAGE / "sets" / f"{n}.js").read_text()) for n in sets.BUILTIN]:
                f = pathlib.Path(tmp, f"{name}.js")
                f.write_text(text)
                p = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, f"{name}: {p.stderr}")


if __name__ == "__main__":
    unittest.main()
