#!/usr/bin/env python3
"""Serves the scene and holds the live-chat message log (stdlib only).

Endpoints:
  GET  /                     -> scene/index.html
  GET  /api/messages?since=N -> {"messages":[...], "last": <maxid>}
  POST /api/messages         -> append {who,text,kind}; returns {"id": <n>}
"""
import json, os, threading, time, subprocess, wave
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import ai

PIPER_BIN = os.environ.get("PIPER_BIN", "/opt/piper/piper/piper")
PIPER_VOICE = os.environ.get("PIPER_VOICE", "/opt/piper/voices/en_US-amy-medium.onnx")
TTS_DIR = "/tmp/ttscache"
os.makedirs(TTS_DIR, exist_ok=True)


def speak(text):
    """Synthesize `text` with Piper into a served WAV. Returns (url, ms).

    The browser fetches and plays this WAV, driving both the stream audio
    (chromium -> pulse -> ffmpeg) and the mouth animation from one timeline.
    """
    if not os.path.exists(PIPER_BIN):
        return None, 0
    name = f"{int(time.time()*1000)}.wav"
    path = os.path.join(TTS_DIR, name)
    try:
        subprocess.run([PIPER_BIN, "-m", PIPER_VOICE, "-f", path],
                       input=text.encode(), timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with wave.open(path) as w:
            ms = int(1000 * w.getnframes() / w.getframerate())
        return f"/api/tts/{name}", ms
    except Exception as e:
        print(f"[tts] synth failed: {e}")
        return None, 0

SCENE_DIR = os.path.join(os.path.dirname(__file__), "scene")
CHARTS_DIR = os.path.join(os.path.dirname(__file__), "charts")
LOG_PATH = os.path.join(os.path.dirname(__file__), "logs", "replies.log")

_MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
         ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
         ".ico": "image/x-icon"}
_lock = threading.Lock()
_messages = []          # each: {id, who, text, kind, ts}
_next_id = 1


def add_message(who, text, kind, audio_ms=0, audio_url=None):
    global _next_id
    with _lock:
        msg = {"id": _next_id, "who": who, "text": text, "kind": kind,
               "audio_ms": audio_ms, "audio_url": audio_url}
        _messages.append(msg)
        _next_id += 1
        # keep memory bounded
        if len(_messages) > 200:
            del _messages[:-200]
        return msg["id"]


def messages_since(since):
    with _lock:
        out = [m for m in _messages if m["id"] > since]
        last = _messages[-1]["id"] if _messages else since
        return out, last


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/messages":
            since = int((parse_qs(parsed.query).get("since", ["0"])[0]) or 0)
            msgs, last = messages_since(since)
            return self._json(200, {"messages": msgs, "last": last})
        if parsed.path.startswith("/api/tts/"):
            name = os.path.basename(parsed.path)
            fp = os.path.join(TTS_DIR, name)
            if not os.path.isfile(fp):
                self.send_error(404); return
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return

        # static: /charts/* -> the trading app, everything else -> scene/
        if parsed.path.startswith("/charts/"):
            base, rel = CHARTS_DIR, parsed.path[len("/charts/"):]
        else:
            base = SCENE_DIR
            rel = "index.html" if parsed.path in ("/", "") else parsed.path.lstrip("/")
        fp = os.path.normpath(os.path.join(base, rel))
        if not fp.startswith(base) or not os.path.isfile(fp):
            self.send_error(404)
            return
        ctype = _MIME.get(os.path.splitext(fp)[1], "application/octet-stream")
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/messages", "/api/ask"):
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})

        if path == "/api/messages":
            mid = add_message(payload.get("who", "?"), payload.get("text", ""),
                              payload.get("kind", "viewer"))
            return self._json(200, {"id": mid})

        # /api/ask : post the question, get an AI reply, post it back
        who = (payload.get("who") or "you").strip()[:24]
        text = (payload.get("text") or "").strip()[:400]
        if not text:
            return self._json(400, {"error": "empty question"})
        add_message(who, text, "viewer")
        reply, source = ai.generate(text)
        url, ms = speak(reply)
        add_message("AI host", reply, "ai", audio_ms=ms, audio_url=url)
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} | {who}: {text} | "
                        f"AI({source}): {reply}\n")
        except Exception:
            pass
        self._json(200, {"reply": reply, "source": source})


if __name__ == "__main__":
    port = int(os.environ.get("SCENE_PORT", "8080"))
    add_message("AI host", "Live on the gold desk. Ask me anything in chat.", "ai")
    print(f"[scene_server] listening on :{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
