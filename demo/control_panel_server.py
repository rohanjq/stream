#!/usr/bin/env python3
"""Serve the operator console and proxy its requests to the scene API."""
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


CONTROL_DIR = os.path.join(os.path.dirname(__file__), "control")
SCENE_API = os.environ.get("SCENE_API", "http://127.0.0.1:8080").rstrip("/")
COMPOSITOR_API = os.environ.get(
    "COMPOSITOR_API", "http://127.0.0.1:7800").rstrip("/")
UPSTREAM_TOKEN = os.environ.get("CONTROL_TOKEN", "")
TRUST_LOCAL_OPERATOR = os.environ.get(
    "OPERATOR_TRUST_LOCAL",
    "true" if os.environ.get("STREAM_BIND_ADDRESS", "127.0.0.1")
    in ("127.0.0.1", "::1", "localhost") else "false",
).lower() in ("1", "true", "yes")
MAX_BODY_BYTES = 1024 * 1024
MIME_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8",
              cache_control="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json_error(self, status, message):
        self._send(status, json.dumps({"error": message}).encode())

    def _proxy(self, upstream, path):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_BODY_BYTES:
            return self._json_error(413, "request body is too large")
        body = self.rfile.read(length) if length else None
        headers = {"Accept": self.headers.get("Accept", "application/json")}
        for name in ("Authorization", "Content-Type"):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        if "Authorization" not in headers and TRUST_LOCAL_OPERATOR and UPSTREAM_TOKEN:
            headers["Authorization"] = f"Bearer {UPSTREAM_TOKEN}"
        request = urllib.request.Request(
            upstream + path, data=body, headers=headers,
            method=self.command)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = response.read()
                content_type = response.headers.get(
                    "Content-Type", "application/octet-stream")
                return self._send(response.status, payload, content_type)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            content_type = exc.headers.get(
                "Content-Type", "application/json; charset=utf-8")
            return self._send(exc.code, payload, content_type)
        except urllib.error.URLError:
            return self._json_error(502, "stream control API is unavailable")

    def _serve_static(self):
        path = urlparse(self.path).path
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        candidate = os.path.realpath(os.path.join(CONTROL_DIR, relative))
        if os.path.commonpath((os.path.realpath(CONTROL_DIR), candidate)) != os.path.realpath(CONTROL_DIR):
            return self._json_error(404, "not found")
        if not os.path.isfile(candidate):
            return self._json_error(404, "not found")
        with open(candidate, "rb") as stream:
            body = stream.read()
        content_type = MIME_TYPES.get(
            os.path.splitext(candidate)[1], "application/octet-stream")
        self._send(200, body, content_type, "no-cache")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            return self._send(200, b'{"status":"ok"}')
        if path == "/panel-config":
            body = json.dumps({"local_access": TRUST_LOCAL_OPERATOR}).encode()
            return self._send(200, body)
        if path.startswith("/compositor-api/"):
            target = self.path.replace("/compositor-api", "", 1)
            return self._proxy(COMPOSITOR_API, target)
        if path.startswith("/api/"):
            return self._proxy(SCENE_API, self.path)
        return self._serve_static()

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/compositor-api/"):
            target = self.path.replace("/compositor-api", "", 1)
            return self._proxy(COMPOSITOR_API, target)
        if not path.startswith("/api/"):
            return self._json_error(404, "not found")
        return self._proxy(SCENE_API, self.path)


if __name__ == "__main__":
    port = int(os.environ.get("OPERATOR_PORT", "8082"))
    print(f"[control-panel] listening on :{port}; scene={SCENE_API}; "
          f"compositor={COMPOSITOR_API}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()