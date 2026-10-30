import os
import pathlib
import tempfile
import unittest

from fleet_kit import accounts, model

HERE = pathlib.Path(__file__).resolve().parent
MINIMAL = HERE.parent / "examples" / "minimal.toml"


def load(admin=None, courier=False):
    _, doc = model.load(MINIMAL)
    doc["fleet"]["agents_dir"] = "/srv/agents"
    if admin:
        doc["machines"]["box"]["admin"] = admin
    if courier:
        doc["machines"]["laptop"] = {"role": "guard", "family": "nixos", "home_root": "/srv",
                                     "courier": {"to": "box", "every": "15m"}}
        doc["guards"] = {"lap-guard": {"machine": "laptop", "apps": ["x"]}}
    return doc


class Recorder:
    def __init__(self, users=(), groups=()):
        self.users = set(users)
        self.groups = set(groups)
        self.calls = []

    def run(self, argv):
        self.calls.append(argv)
        if argv[0] == "useradd":
            self.users.add(argv[-1])
        if argv[0] == "groupadd":
            self.groups.add(argv[1])
        return ""


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def apply(self, doc, rec, **kw):
        return accounts.apply_accounts(doc, "box", root=self.root, run=rec.run,
                                       user_exists=lambda u: u in rec.users, group_exists=lambda g: g in rec.groups, **kw)

    def test_fresh_machine(self):
        rec = Recorder(users={"erik"})
        acts = self.apply(load(admin="erik"), rec)
        cmds = [c[0] for c in rec.calls]
        self.assertIn("groupadd", cmds)
        self.assertEqual(sum(1 for c in rec.calls if c[0] == "useradd"), 2)
        ua = next(c for c in rec.calls if c[0] == "useradd" and c[-1] == "myapp-dev")
        self.assertIn("/srv/myapp-dev", ua)
        self.assertIn(["usermod", "-aG", "agents", "erik"], rec.calls)
        self.assertIn(["usermod", "-aG", "myapp-dev", "erik"], rec.calls)
        self.assertIn(["loginctl", "enable-linger", "myapp-plan"], rec.calls)
        for d, mode in (("srv/agents", 0o2775), ("srv/agents/status", 0o2775), ("srv/myapp-dev", 0o750), ("data/myapp-dev", 0o750)):
            self.assertTrue((self.root / d).is_dir(), d)
            self.assertEqual((self.root / d).stat().st_mode & 0o7777, mode, d)
        self.assertFalse((self.root / "data/myapp-plan").exists())   # a plan bot builds nothing
        self.assertEqual(os.readlink(self.root / "srv/myapp-plan/agents"), "/srv/agents")
        conf = (self.root / "srv/myapp-dev/.config/containers/storage.conf").read_text()
        self.assertIn('graphroot = "/data/myapp-dev/containers"', conf)
        self.assertTrue(any(w == "skipped" and "trial" in x for w, x in acts))

    def test_second_run_keeps(self):
        rec = Recorder(users={"erik"})
        self.apply(load(admin="erik"), rec)
        rec.calls.clear()
        acts = self.apply(load(admin="erik"), rec)
        self.assertEqual([c[0] for c in rec.calls if c[0] in ("useradd", "groupadd")], [])
        kinds = {w for w, _ in acts}
        self.assertNotIn("added", kinds)
        self.assertNotIn("changed", kinds)
        # storage.conf is never rewritten
        (self.root / "srv/myapp-dev/.config/containers/storage.conf").write_text("mine\n")
        self.apply(load(admin="erik"), rec)
        self.assertEqual((self.root / "srv/myapp-dev/.config/containers/storage.conf").read_text(), "mine\n")

    def test_no_admin_is_said(self):
        rec = Recorder()
        acts = self.apply(load(), rec)
        self.assertTrue(any(w == "skipped" and "no admin" in x for w, x in acts))
        self.assertFalse(any(c[0] == "usermod" for c in rec.calls))

    def test_courier_guard_gets_an_account(self):
        rec = Recorder()
        self.apply(load(courier=True), rec)
        ua = [c[-1] for c in rec.calls if c[0] == "useradd"]
        self.assertIn("lap-guard", ua)
        self.assertEqual(next(c for c in rec.calls if c[0] == "useradd" and c[-1] == "lap-guard")[-2], "guard lap-guard (fleet)")

    def test_dry_run_and_other_machines(self):
        rec = Recorder()
        acts = self.apply(load(), rec, dry_run=True)
        self.assertFalse((self.root / "srv/myapp-dev").exists())
        self.assertIn(["useradd", "--create-home", "--home-dir", "/srv/myapp-dev", "--groups", "agents", "--shell", "/bin/bash",
                       "--comment", "bot myapp-dev (fleet)", "myapp-dev"], rec.calls)
        self.assertTrue(any(w == "added" and "account myapp-dev" in x for w, x in acts))
        doc = load()
        doc["machines"]["box"]["family"] = "nixos"
        self.assertEqual(self.apply(doc, Recorder())[0][0], "skipped")
        self.assertEqual(accounts.apply_accounts(load(), "nowhere", root=self.root)[0][0], "skipped")

    def test_link_in_the_way_is_left(self):
        rec = Recorder(users={"myapp-dev", "myapp-plan"})
        (self.root / "srv/myapp-dev").mkdir(parents=True)
        (self.root / "srv/myapp-dev/agents").mkdir()
        acts = self.apply(load(), rec)
        self.assertIn(("skipped", "/srv/myapp-dev/agents -> /srv/agents"), acts)


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def apply(self, doc, rec, **kw):
        return accounts.apply_inbox(doc, "box", root=self.root, run=rec.run,
                                    user_exists=lambda u: u in rec.users, group_exists=lambda g: g in rec.groups, **kw)

    def test_inboxes_with_acls(self):
        rec = Recorder(users={"myapp-dev", "myapp-plan", "erik"})
        acts = self.apply(load(admin="erik"), rec)
        for name in ("myapp-dev", "myapp-plan"):
            d = self.root / "srv/agents/inbox" / name
            self.assertTrue(d.is_dir())
            self.assertEqual(d.stat().st_mode & 0o7777, 0o1730)
            self.assertTrue((d / "done").is_dir())
            self.assertEqual((d / "done").stat().st_mode & 0o777, 0o700)
            self.assertFalse((d / "sent").exists())
        self.assertIn(["setfacl", "-m", "u:erik:rwx", "/srv/agents/inbox/myapp-dev"], rec.calls)
        self.assertIn(["setfacl", "-d", "-m", "u::rwx,u:myapp-dev:rwx,u:erik:rwx,g::-,o::-", "/srv/agents/inbox/myapp-dev"], rec.calls)
        self.assertFalse(any(c[0] == "find" for c in rec.calls))   # fresh inboxes: nothing to chown inside
        self.assertTrue(any(w == "added" and "1730" in x for w, x in acts))

    def test_existing_inbox_content_is_the_recipients(self):
        rec = Recorder(users={"myapp-dev", "myapp-plan"})
        d = self.root / "srv/agents/inbox/myapp-plan"
        d.mkdir(parents=True)
        (d / "note.md").write_text("hi\n")
        self.apply(load(admin="erik"), rec)
        self.assertIn(["find", "/srv/agents/inbox/myapp-plan", "-mindepth", "1", "-exec", "chown", "-h", "myapp-plan:myapp-plan", "{}", "+"], rec.calls)
        self.assertTrue(any(c[0] == "find" and "u:erik:rwX" in c for c in rec.calls))

    def test_courier_owns_the_remote_inboxes(self):
        rec = Recorder(users={"myapp-dev", "myapp-plan", "lap-guard"})
        doc = load(courier=True)
        acts = self.apply(doc, rec)
        self.assertTrue(any(w != "skipped" and "/srv/agents/inbox/lap-guard (1730, lap-guard:agents)" in x for w, x in acts))
        self.assertTrue((self.root / "srv/agents/inbox/lap-guard/sent").is_dir())   # the courier keeps carried mail
        self.assertFalse((self.root / "srv/agents/inbox/myapp-dev/sent").exists())

    def test_missing_account_is_skipped(self):
        rec = Recorder(users={"myapp-plan"})
        acts = self.apply(load(), rec)
        self.assertTrue(any(w == "skipped" and "myapp-dev missing" in x for w, x in acts))
        self.assertFalse((self.root / "srv/agents/inbox/myapp-dev").exists())
        self.assertTrue((self.root / "srv/agents/inbox/myapp-plan").is_dir())

    def test_second_run_keeps_dirs(self):
        rec = Recorder(users={"myapp-dev", "myapp-plan"})
        self.apply(load(), rec)
        acts = self.apply(load(), rec)
        self.assertEqual({w for w, x in acts if "inbox/myapp" in x and "(" in x}, {"kept"})


if __name__ == "__main__":
    unittest.main()
