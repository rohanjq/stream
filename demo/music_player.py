#!/usr/bin/env python3
"""Single-owner playlist player with a loopback HTTP control API."""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


_installed_catalog = "/opt/app/music_catalog.json"
CATALOG_PATH = os.environ.get(
    "MUSIC_CATALOG",
    _installed_catalog if os.path.exists(_installed_catalog)
    else os.path.join(os.path.dirname(__file__), "music_catalog.json"))
PORT = int(os.environ.get("MUSIC_CONTROL_PORT", "8091"))
PULSE_SERVER = os.environ.get("PULSE_SERVER", "unix:/tmp/xdgr/pulse/native")
STATE_DIR = os.environ.get("STATE_DIR", "/tmp/nightshift-state")
PERSIST_STATE = os.environ.get("PERSIST_STATE", "true").lower() not in ("0", "false", "no")
STATE_PATH = os.path.join(STATE_DIR, "music.json")

with open(CATALOG_PATH, encoding="utf-8") as stream:
    CATALOG = json.load(stream)
if not CATALOG:
    raise RuntimeError("music catalog is empty")


class Player:
    def __init__(self):
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.index = 0
        self.playing = True
        self.volume = max(0.0, min(1.0, float(os.environ.get("MUSIC_VOLUME", "0.35"))))
        self.process = None
        self.started_at = None
        self.error = None
        self.sink_input = None
        self._restore()

    def _restore(self):
        if not PERSIST_STATE or not os.path.isfile(STATE_PATH):
            return
        try:
            with open(STATE_PATH, encoding="utf-8") as stream:
                saved = json.load(stream)
            track_id = saved.get("track_id")
            match = next((i for i, track in enumerate(CATALOG)
                          if track["id"] == track_id), None)
            if match is not None:
                self.index = match
            if isinstance(saved.get("playing"), bool):
                self.playing = saved["playing"]
            volume = float(saved.get("volume", self.volume))
            self.volume = max(0.0, min(1.0, volume))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"[music] ignored invalid persisted state: {exc}", flush=True)

    def _persist_locked(self):
        if not PERSIST_STATE:
            return
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            temporary = STATE_PATH + ".tmp"
            payload = {
                "track_id": CATALOG[self.index]["id"],
                "playing": self.playing,
                "volume": self.volume,
            }
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, STATE_PATH)
        except OSError as exc:
            print(f"[music] could not persist state: {exc}", flush=True)

    def _public_track(self, track):
        return {key: value for key, value in track.items()
                if key not in ("download_url", "file", "sha256")}

    def status(self):
        with self.lock:
            return {
                "playing": self.playing,
                "volume": self.volume,
                "index": self.index,
                "count": len(CATALOG),
                "track": self._public_track(CATALOG[self.index]),
                "started_at": self.started_at,
                "elapsed_seconds": max(0, int(time.time() - self.started_at))
                    if self.started_at and self.playing else 0,
                "error": self.error,
                "sink_input": self.sink_input,
            }

    def catalog(self):
        return [self._public_track(track) for track in CATALOG]

    def _select(self, index):
        with self.lock:
            self.index = index % len(CATALOG)
            self.playing = True
            self.error = None
            self._persist_locked()
        self.changed.set()
        return self.status()

    def play(self, query):
        if query is None:
            raise ValueError("song title is required")
        needle = " ".join(str(query).lower().replace("_", " ").split())
        exact = [i for i, track in enumerate(CATALOG)
                 if needle in (track["id"].lower(), track["title"].lower())]
        partial = [i for i, track in enumerate(CATALOG)
                   if needle and (needle in track["id"].lower().replace("_", " ") or
                                  needle in track["title"].lower())]
        matches = exact or partial
        if not matches:
            raise ValueError("unknown song")
        return self._select(matches[0])

    def next(self):
        with self.lock:
            index = self.index + 1
        return self._select(index)

    def previous(self):
        with self.lock:
            index = self.index - 1
        return self._select(index)

    def pause(self):
        with self.lock:
            self.playing = False
            self._persist_locked()
        self.changed.set()
        return self.status()

    def resume(self):
        with self.lock:
            self.playing = True
            self._persist_locked()
        self.changed.set()
        return self.status()

    def set_volume(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError("volume must be between 0 and 1")
        with self.lock:
            self.volume = value
            process = self.process
            self._persist_locked()
        if process and process.poll() is None:
            self._apply_volume(process, value, attempts=3, timeout=4)
        return self.status()

    def _sink_input_for_pid(self, process_id, timeout=3):
        try:
            result = subprocess.run(
                ["pactl", "list", "sink-inputs"], env={
                    **os.environ, "PULSE_SERVER": PULSE_SERVER,
                }, capture_output=True, text=True, timeout=timeout, check=True)
        except (OSError, subprocess.SubprocessError):
            return None
        current = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Sink Input #"):
                try:
                    current = int(stripped.removeprefix("Sink Input #"))
                except ValueError:
                    current = None
            elif (current is not None and
                  stripped == f'application.process.id = "{process_id}"'):
                return current
        return None

    def _apply_volume(self, process, volume, attempts=1, timeout=4):
        deadline = time.monotonic() + timeout
        for attempt in range(attempts):
            if process.poll() is not None:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            sink_input = self._sink_input_for_pid(process.pid, timeout=remaining)
            if sink_input is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                try:
                    subprocess.run([
                        "pactl", "set-sink-input-volume", str(sink_input),
                        f"{volume * 100:.2f}%",
                    ], env={**os.environ, "PULSE_SERVER": PULSE_SERVER},
                       capture_output=True, timeout=remaining, check=True)
                    with self.lock:
                        if self.process is process:
                            self.sink_input = sink_input
                    return True
                except (OSError, subprocess.SubprocessError):
                    return False
            if attempt + 1 < attempts:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        return False

    def run(self):
        while True:
            with self.lock:
                playing = self.playing
                track = CATALOG[self.index]
                volume = self.volume
            if not playing:
                self.changed.wait(1)
                self.changed.clear()
                continue
            command = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", track["file"], "-af", "aresample=48000",
                "-ar", "48000", "-ac", "2", "-f", "pulse", "ytsink",
            ]
            env = os.environ.copy()
            env["PULSE_SERVER"] = PULSE_SERVER
            try:
                with self.lock:
                    self.started_at = time.time()
                    self.error = None
                    self.sink_input = None
                    self.process = subprocess.Popen(command, env=env)
                    process = self.process
                self._apply_volume(process, volume, attempts=20)
                while self.process.poll() is None and not self.changed.wait(0.25):
                    pass
                switched = self.changed.is_set()
                self.changed.clear()
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=3)
                with self.lock:
                    self.process = None
                    self.sink_input = None
                if not switched:
                    self.next()
                    self.changed.clear()
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)[:240]
                    self.process = None
                time.sleep(2)


PLAYER = Player()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/status":
            return self.reply(200, PLAYER.status())
        if path == "/catalog":
            return self.reply(200, {"tracks": PLAYER.catalog()})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/play":
                return self.reply(200, PLAYER.play(payload.get("id") or payload.get("query")))
            if path == "/next":
                return self.reply(200, PLAYER.next())
            if path == "/previous":
                return self.reply(200, PLAYER.previous())
            if path == "/pause":
                return self.reply(200, PLAYER.pause())
            if path == "/resume":
                return self.reply(200, PLAYER.resume())
            if path == "/volume":
                return self.reply(200, PLAYER.set_volume(payload.get("volume")))
            return self.reply(404, {"error": "not found"})
        except (TypeError, ValueError) as exc:
            return self.reply(400, {"error": str(exc)})


if __name__ == "__main__":
    threading.Thread(target=PLAYER.run, daemon=True, name="music-playback").start()
    print(f"[music] serving {len(CATALOG)} tracks on 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
