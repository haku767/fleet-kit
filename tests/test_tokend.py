import importlib.machinery
import importlib.util
import json
import os
import pathlib
import socket
import tempfile
import threading
import tomllib
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "src" / "fleet_kit" / "data"
SOLO = HERE.parent / "examples" / "solo.toml"


def load(name):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, str(DATA / name.replace("_", "-"))))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


UIDS = {"myapp-dev": 1201, "fleet-review": 1200, "box-guard": 1300}


def getpwnam(name):
    if name in UIDS:
        return UIDS[name]
    raise KeyError(name)


def doc():
    d = tomllib.loads(SOLO.read_text())
    d["guards"] = {"box-guard": {"machine": "box", "apps": ["x"], "writes": ["notes"]}}
    d["machines"]["box"]["guard"] = "box-guard"
    return d


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.td = load("fleet_tokend")
        self.fleet = self.td.Fleet(doc(), getpwnam=getpwnam)

    def test_dev_bot_gets_its_repository_with_write(self):
        slug, repos, perms = self.fleet.grant(1201)
        self.assertEqual((slug, repos), ("example-fleet-dev", ["myapp"]))
        self.assertEqual(perms["contents"], "write")

    def test_reviewer_gets_every_reviewed_repository(self):
        slug, repos, perms = self.fleet.grant(1200)
        self.assertEqual((slug, repos), ("example-fleet-review", ["myapp"]))
        self.assertEqual(perms["contents"], "read")
        self.assertEqual(perms["pull_requests"], "write")

    def test_guard_reads_everything_and_writes_where_listed(self):
        slug, repos, perms = self.fleet.grant(1300)
        self.assertEqual((slug, repos, perms), ("example-fleet-review", None, self.td.REVIEW))
        slug, repos, perms = self.fleet.grant(1300, "dev")
        self.assertEqual((slug, repos), ("example-fleet-dev", ["notes"]))
        self.assertEqual(perms, self.td.WRITE)

    def test_unknown_uid_is_not_served(self):
        self.assertTrue(str(self.fleet.grant(4242)).startswith("not served"))

    def test_foreign_owner_needs_its_app(self):
        d = doc()
        d["projects"]["other"] = {"repo": "someone/other", "kind": "solo", "stacks": ["python-uv"], "dev": {"name": "other-dev"}}
        UIDS["other-dev"] = 1202
        f = self.td.Fleet(d, getpwnam=getpwnam)
        self.assertIn("no App for someone", f.grant(1202))
        d["apps"]["someone"] = {"owner": "someone", "slug": "someone-fleet"}
        f = self.td.Fleet(d, getpwnam=getpwnam)
        self.assertEqual(f.grant(1202)[:2], ("someone-fleet", ["other"]))


class FakeApi:
    def __init__(self):
        self.calls = []
        self.n = 0

    def __call__(self, method, path, auth, data=None):
        self.calls.append((method, path, data))
        if path == "/app":
            assert auth.startswith("Bearer "), auth
            return {"slug": "example-fleet-dev"}
        if path.startswith("/users/"):
            assert auth.startswith("token ghs_"), auth   # the bot user needs an installation token, not the JWT
            return {"id": 777}
        if path == "/app/installations":
            return [{"id": 5, "account": {"login": "Example"}}]
        if path.endswith("/access_tokens"):
            self.n += 1
            return {"token": f"ghs_{self.n}", "expires_at": "2099-01-01T00:00:00Z"}
        raise AssertionError(path)


class MinterTests(unittest.TestCase):
    def setUp(self):
        self.td = load("fleet_tokend")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.td.jwt = lambda keys, slug: "jwt"   # no openssl, no key files
        self.api = FakeApi()
        self.minter = self.td.Minter(self.tmp.name, api=self.api, now=lambda: 0)

    def test_scoped_body_and_cache(self):
        t = self.minter.token("example-fleet-dev", "example", ["myapp"], self.td.WRITE)
        self.assertEqual(t["token"], "ghs_1")
        self.assertEqual((t["slug"], t["bot_id"]), ("example-fleet-dev", 777))
        post = next(c for c in self.api.calls if c[0] == "POST")
        body = json.loads(post[2])
        self.assertEqual(body["repositories"], ["myapp"])
        self.assertEqual(body["permissions"]["contents"], "write")
        self.assertEqual(self.minter.token("example-fleet-dev", "example", ["myapp"], self.td.WRITE)["token"], "ghs_1")   # cached
        self.assertEqual(self.minter.token("example-fleet-dev", "example", ["other"], self.td.WRITE)["token"], "ghs_2")

    def test_all_repositories_sends_no_list(self):
        self.minter.token("example-fleet-dev", "example", None, self.td.READ)
        post = next(c for c in self.api.calls if c[0] == "POST")
        self.assertNotIn("repositories", json.loads(post[2]))

    def test_answer_reports_errors(self):
        fleet = self.td.Fleet(doc(), getpwnam=getpwnam)
        self.assertEqual(self.td.answer(fleet, self.minter, 4242, {})["error"][:10], "not served")
        self.assertIn("token", self.td.answer(fleet, self.minter, 1201, {"app": None}))
        read = self.td.answer(fleet, self.minter, 1201, {"scope": "read"})
        post = [c for c in self.api.calls if c[0] == "POST"][-1]
        self.assertEqual(json.loads(post[2])["permissions"]["contents"], "read")
        self.assertIn("token", read)


class ClientTests(unittest.TestCase):
    """fleet-token asks the socket when it exists and falls back to its own keys when it is told
    "not served" or finds no socket."""

    def setUp(self):
        self.ft = load("fleet_token")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sock = os.path.join(self.tmp.name, "token.sock")

    def serve(self, reply):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.sock)
        srv.listen(1)

        def run():
            c, _ = srv.accept()
            with c:
                c.makefile("rb").readline()
                c.sendall((json.dumps(reply) + "\n").encode())
            srv.close()
        threading.Thread(target=run, daemon=True).start()

    def test_no_socket_means_none(self):
        self.assertIsNone(self.ft.from_service(path=self.sock))

    def test_token_from_the_service(self):
        self.serve({"token": "ghs_x", "until": 1, "slug": "s", "bot_id": 1})
        self.assertEqual(self.ft.from_service(path=self.sock)["token"], "ghs_x")

    def test_not_served_falls_back(self):
        self.serve({"error": "not served: this account is no bot"})
        self.ft.SOCK = self.sock
        called = []
        self.ft.jwt = lambda conf=None: called.append(conf) or "jwt"
        self.ft.api = lambda method, path, auth, data=None: ([{"id": 1, "account": {"login": "x"}}] if path == "/app/installations"
                                                            else {"owner": {"login": "x"}} if path == "/app"
                                                            else {"token": "ghs_local", "expires_at": "2099-01-01T00:00:00Z"})
        self.ft.CACHE = pathlib.Path(self.tmp.name) / "cache.json"
        self.assertEqual(self.ft.token(), "ghs_local")
        self.assertTrue(called)


if __name__ == "__main__":
    unittest.main()


class ApplyTests(unittest.TestCase):
    def test_apply_rounds_installs_the_service(self):
        from fleet_kit import rounds
        d = doc()
        d["machines"]["box"]["family"] = "arch"
        calls, state = [], {}

        def sc(args):
            calls.append(args)
            if args[0] == "show":
                st = state.get(args[1], ("disabled", "inactive"))
                return f"UnitFileState={st[0]}\nActiveState={st[1]}\n"
            if args[0] == "enable":
                state[args[1]] = ("enabled", "inactive")
            return ""
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            (root / "srv/agents").mkdir(parents=True)
            acts = rounds.apply(d, "box", root=root, systemctl=sc)
            self.assertTrue((root / "usr/local/bin/fleet-tokend").is_file())
            unit = (root / "etc/systemd/system/fleet-tokend.service").read_text()
            self.assertIn("User=fleet", unit)
            self.assertIn(("enabled", "fleet-tokend.service"), acts)
            self.assertTrue(any("not started" in w for _, w in acts), acts)

    def test_fleet_json_names_the_service(self):
        from fleet_kit import render
        fj = json.loads(render.render(doc())["machines/box/fleet.json"])
        self.assertEqual(fj["token_service"]["account"], "fleet")
        self.assertEqual(fj["token_service"]["slugs"], ["example-fleet-dev", "example-fleet-review"])


class CredentialHelperTests(unittest.TestCase):
    def test_app_flag_before_the_operation(self):
        import contextlib
        import io
        ft = load("fleet_token")
        ft.token = lambda app=None, scope=None: f"tok-{app}"
        for argv, want in ((["git-credential", "get"], "tok-None"), (["git-credential", "--app", "dev", "get"], "tok-dev")):
            out = io.StringIO()
            with mock.patch("sys.stdin", io.StringIO("protocol=https\nhost=github.com\n")), mock.patch("sys.argv", ["fleet-token", *argv]), contextlib.redirect_stdout(out):
                ft.main()
            self.assertIn(f"password={want}", out.getvalue())
