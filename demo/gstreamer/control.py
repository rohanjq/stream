"""Control HTTP server — identical endpoint contract to the Smelter version's
controlServer.ts, so switching backends doesn't change how you drive it.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import state as st

LAYOUTS = {"main", "logs", "poll", "leaderboard"}
CONTROL_TOKEN = os.environ.get("CONTROL_TOKEN", "")
_health_provider = lambda: {"status": "starting"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    def _authorized(self):
        if not CONTROL_TOKEN:
            return self.client_address[0] in ("127.0.0.1", "::1")
        return self.headers.get("Authorization", "") == f"Bearer {CONTROL_TOKEN}"

    def do_GET(self):
        if self.path == "/health":
            health = _health_provider()
            return self._json(200 if health.get("status") != "stalled" else 503, health)
        if self.path == "/state":
            return self._json(200, st.snapshot())
        self.send_error(404)

    def do_POST(self):
        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})
        try:
            body = self._body()
        except Exception:
            return self._json(400, {"error": "bad json"})

        if self.path == "/layout":
            if body.get("layout") not in LAYOUTS:
                return self._json(400, {"error": f"layout must be one of {sorted(LAYOUTS)}"})
            st.set_layout(body["layout"])
            return self._json(200, {"ok": True})

        if self.path == "/log":
            if not body.get("text"):
                return self._json(400, {"error": "text required"})
            st.push_log(str(body["text"]))
            return self._json(200, {"ok": True})

        if self.path == "/poll":
            if not body.get("question") or not isinstance(body.get("options"), list):
                return self._json(400, {"error": "question and options[] required"})
            st.set_poll(str(body["question"]), [str(o) for o in body["options"]])
            return self._json(200, {"ok": True})

        if self.path == "/poll/vote":
            if not body.get("option"):
                return self._json(400, {"error": "option required"})
            st.vote_poll(str(body["option"]))
            return self._json(200, {"ok": True})

        if self.path == "/leaderboard":
            if not isinstance(body.get("entries"), list):
                return self._json(400, {"error": "entries[] required"})
            st.set_leaderboard(body["entries"])
            return self._json(200, {"ok": True})

        self.send_error(404)


def start(port, health_provider=None):
    global _health_provider
    if health_provider is not None:
        _health_provider = health_provider
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[control] listening on :{port}")
    return server
