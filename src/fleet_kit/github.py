"""GitHub through `gh api`: the caller's own login or App, never a token of the kit's.

Every call is one `gh api` process; a failure raises GHError with gh's message. Tests pass
a fake with the same four methods."""

import json
import subprocess


class GHError(Exception):
    pass


class GH:
    def _run(self, method, path, body=None, paginate=False):
        cmd = ["gh", "api", "-X", method, path]
        if paginate:
            cmd += ["--paginate", "--slurp"]
        if body is not None:
            cmd += ["--input", "-"]
        try:
            r = subprocess.run(cmd, input=json.dumps(body) if body is not None else None,
                               capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            raise GHError("gh is not installed")
        except subprocess.TimeoutExpired:
            raise GHError(f"gh api {path}: no answer in 120 s")
        if r.returncode != 0:
            msg = r.stderr.strip() or r.stdout.strip()
            try:
                msg = json.loads(r.stdout).get("message") or msg
            except (ValueError, AttributeError):
                pass
            msg = msg.removeprefix("gh: ")
            if "Not Found" in msg:
                msg += " (no such repository, or your login or App cannot see it)"
            raise GHError(f"gh api {method} {path}: {msg}")
        if not r.stdout.strip():
            return None
        out = json.loads(r.stdout)
        if paginate:   # --slurp: one array per page
            out = [x for page in out for x in page]
        return out

    def get(self, path, paginate=False):
        return self._run("GET", path, paginate=paginate)

    def post(self, path, body):
        return self._run("POST", path, body)

    def patch(self, path, body):
        return self._run("PATCH", path, body)

    def put(self, path, body):
        return self._run("PUT", path, body)
