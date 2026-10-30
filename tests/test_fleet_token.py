import importlib.machinery
import importlib.util
import io
import json
import pathlib
import unittest
import urllib.error
from unittest import mock

DATA = pathlib.Path(__file__).resolve().parent.parent / "src" / "fleet_kit" / "data" / "fleet-token"


def load():
    spec = importlib.util.spec_from_loader("fleet_token", importlib.machinery.SourceFileLoader("fleet_token", str(DATA)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.ft = load()

    def test_timeouts_are_retried_then_answered(self):
        calls = []

        def urlopen(req, timeout=0):
            calls.append(req.full_url)
            if len(calls) < 3:
                raise urllib.error.URLError("_ssl.c:1064: The handshake operation timed out")
            return Answer(json.dumps({"ok": True}).encode())

        with mock.patch.object(self.ft.urllib.request, "urlopen", urlopen), mock.patch.object(self.ft.time, "sleep") as sleep:
            self.assertEqual(self.ft.api("GET", "/app", "Bearer x"), {"ok": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 5])

    def test_gives_up_after_three(self):
        def urlopen(req, timeout=0):
            raise urllib.error.URLError("timed out")

        with mock.patch.object(self.ft.urllib.request, "urlopen", urlopen), mock.patch.object(self.ft.time, "sleep"):
            with self.assertRaises(urllib.error.URLError):
                self.ft.api("GET", "/app", "Bearer x")

    def test_a_4xx_is_not_retried(self):
        calls = []

        def urlopen(req, timeout=0):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        with mock.patch.object(self.ft.urllib.request, "urlopen", urlopen), mock.patch.object(self.ft.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                self.ft.api("GET", "/app", "Bearer x")
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_a_5xx_is_retried(self):
        calls = []

        def urlopen(req, timeout=0):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)
            return Answer(b'{"token": "t"}')

        with mock.patch.object(self.ft.urllib.request, "urlopen", urlopen), mock.patch.object(self.ft.time, "sleep"):
            self.assertEqual(self.ft.api("POST", "/x", "Bearer x", b""), {"token": "t"})
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
