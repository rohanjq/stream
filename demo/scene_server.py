#!/usr/bin/env python3
"""Serves the scene and holds the live-chat message log (stdlib only).

Endpoints:
  GET  /                     -> scene/index.html
  GET  /api/messages?since=N -> {"messages":[...], "last": <maxid>}
  POST /api/messages         -> append {who,text,kind}; returns {"id": <n>}
"""
import itertools, json, os, queue, threading, time, subprocess, wave, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import ai
import audience_commands
import youtube_chat

PIPER_BIN = os.environ.get("PIPER_BIN", "/opt/piper/piper/piper")
PIPER_VOICE = os.environ.get("PIPER_VOICE", "/opt/piper/voices/en_US-amy-medium.onnx")
KOKORO_MODEL = os.environ.get("KOKORO_MODEL", "/opt/kokoro/kokoro-v1.0.onnx")
KOKORO_VOICES = os.environ.get("KOKORO_VOICES", "/opt/kokoro/voices-v1.0.bin")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_nicole")
TTS_TARGET_I = os.environ.get("TTS_TARGET_I", "-16")
CONTROL_TOKEN = os.environ.get("CONTROL_TOKEN", "")
YOUTUBE_SPEAK_MODE = os.environ.get("YOUTUBE_SPEAK_MODE", "all").lower()
if YOUTUBE_SPEAK_MODE not in ("off", "selective", "all"):
    YOUTUBE_SPEAK_MODE = "selective"
YOUTUBE_SPEAK_COOLDOWN = max(0, int(os.environ.get("YOUTUBE_SPEAK_COOLDOWN", "12")))
AUDIENCE_COMMAND_ROUTER = os.environ.get("AUDIENCE_COMMAND_ROUTER", "hybrid").lower()
SPEECH_GAP_SECONDS = max(0.0, float(os.environ.get("SPEECH_GAP_SECONDS", "2")))
SPEECH_LIPSYNC_LEAD_SECONDS = max(
    0.0, float(os.environ.get("SPEECH_LIPSYNC_LEAD_SECONDS", "0.35")))
TTS_DIR = "/tmp/ttscache"
os.makedirs(TTS_DIR, exist_ok=True)

_kokoro = None
_kokoro_lock = threading.Lock()


def _pulse_env():
    env = os.environ.copy()
    env.setdefault("PULSE_SERVER", "unix:/tmp/xdgr/pulse/native")
    return env


def synthesize(text):
    """Render text to a WAV and return ``(path, URL, duration_ms)``."""
    name = f"{int(time.time()*1000)}.wav"
    path = os.path.join(TTS_DIR, name)
    try:
        if os.path.exists(KOKORO_MODEL) and os.path.exists(KOKORO_VOICES):
            global _kokoro
            from kokoro_onnx import Kokoro
            with _kokoro_lock:
                if _kokoro is None:
                    _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
                samples, sample_rate = _kokoro.create(
                    text, voice=KOKORO_VOICE, speed=1.0, lang="en-us")
                import soundfile as sf
                sf.write(path, samples, sample_rate, subtype="PCM_16")
        else:
            if not os.path.exists(PIPER_BIN) or not os.path.exists(PIPER_VOICE):
                raise RuntimeError("neither Kokoro nor Piper is installed")
            subprocess.run([PIPER_BIN, "-m", PIPER_VOICE, "-f", path],
                           input=text.encode(), timeout=30, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with wave.open(path) as w:
            ms = int(1000 * w.getnframes() / w.getframerate())
        return path, f"/api/tts/{name}", ms
    except Exception as e:
        print(f"[tts] synthesis failed: {e}", flush=True)
        return None, None, 0


def play_audio(path):
    """Play one already-rendered file synchronously into the stream mix."""
    try:
        result = subprocess.run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", path,
            "-af", f"loudnorm=I={TTS_TARGET_I}:TP=-1.5:LRA=11,aresample=48000",
            "-ar", "48000", "-ac", "2", "-f", "pulse", "ytsink",
        ], env=_pulse_env(), timeout=120)
        if result.returncode:
            print(f"[tts] playback failed ({result.returncode}): {path}", flush=True)
            return False
        print(f"[tts] playback complete: {path}", flush=True)
        return True
    except Exception as exc:
        print(f"[tts] playback failed: {exc}", flush=True)
        return False

SCENE_DIR = os.path.join(os.path.dirname(__file__), "scene")
CHARTS_DIR = os.path.join(os.path.dirname(__file__), "charts")
STATE_DIR = os.environ.get("STATE_DIR", "/tmp/nightshift-state")
PERSIST_STATE = os.environ.get("PERSIST_STATE", "true").lower() not in ("0", "false", "no")
os.makedirs(STATE_DIR, exist_ok=True)
LOG_PATH = os.path.join(STATE_DIR, "replies.log")
CONTROL_STATE_PATH = os.path.join(STATE_DIR, "control.json")
YOUTUBE_MESSAGES_PATH = os.path.join(os.path.dirname(__file__), "youtube_messages.json")
AUDIENCE_RULES_PATH = os.path.join(os.path.dirname(__file__), "audience_commands.json")
MUSIC_API = os.environ.get("MUSIC_API", "http://127.0.0.1:8091").rstrip("/")
OHLC_WS_URL = os.environ.get("OHLC_WS_URL", "ws://host.containers.internal:18081/ws")
OHLC_SYMBOL = os.environ.get("OHLC_SYMBOL", "BTCUSDT")


def music_request(path, payload=None):
    """Call the loopback-only player and return its JSON response."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        MUSIC_API + path, data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("error")
        except Exception:
            detail = None
        raise ValueError(detail or f"music player returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("music player is unavailable") from exc

_MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
         ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
         ".ico": "image/x-icon"}
_lock = threading.Lock()
_messages = []          # each: {id, who, text, kind, ts}
_next_id = 1
_youtube_bridge = None
_youtube_policy_lock = threading.Lock()
_youtube_policy = {
    "speak_mode": YOUTUBE_SPEAK_MODE,
    "cooldown_seconds": YOUTUBE_SPEAK_COOLDOWN,
}
_control_lock = threading.Lock()
_control = {
    "revision": 1,
    "updated_at": int(time.time()),
    "source": "startup",
    "layout": "grid",
    "timeframes": ["1m", "5m", "15m", "1h"],
    "show_sidebar": False,
    "overlays": {
        "market_structure": False,
        "key_levels": False,
        "premium_discount": False,
        "fvg": False,
        "order_blocks": False,
        "patterns": False,
        "liquidity": False,
    },
}
VALID_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h"}
VALID_OVERLAYS = set(_control["overlays"])


def _persist_control(state):
    if not PERSIST_STATE:
        return
    try:
        temporary = CONTROL_STATE_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, CONTROL_STATE_PATH)
    except OSError as exc:
        print(f"[control] could not persist state: {exc}", flush=True)


def control_snapshot():
    with _control_lock:
        return {**_control, "timeframes": list(_control["timeframes"]),
                "overlays": dict(_control["overlays"])}


def update_control(payload):
    """Validate and apply one control-plane patch.

    The same contract is suitable for the admin UI, a future Super Chat
    controller, or a scheduler. Callers identify themselves with `source` for
    observability; authentication is handled separately at the HTTP boundary.
    """
    with _control_lock:
        next_state = {**_control, "timeframes": list(_control["timeframes"]),
                      "overlays": dict(_control["overlays"])}
        layout = payload.get("layout")
        if layout is not None:
            if layout not in ("single", "grid"):
                raise ValueError("layout must be single or grid")
            next_state["layout"] = layout

        requested = payload.get("timeframes")
        if "timeframe" in payload:
            requested = [payload["timeframe"]]
            next_state["layout"] = "single"
        if requested is not None:
            if not isinstance(requested, list) or not requested:
                raise ValueError("timeframes must be a non-empty list")
            clean = []
            for tf in requested:
                if tf not in VALID_TIMEFRAMES:
                    raise ValueError(f"unsupported timeframe: {tf}")
                if tf not in clean:
                    clean.append(tf)
            if len(clean) > 4:
                raise ValueError("at most four timeframes are supported")
            next_state["timeframes"] = clean
            if len(clean) == 1:
                next_state["layout"] = "single"

        if "show_sidebar" in payload:
            if not isinstance(payload["show_sidebar"], bool):
                raise ValueError("show_sidebar must be boolean")
            next_state["show_sidebar"] = payload["show_sidebar"]

        requested_overlays = payload.get("overlays")
        if requested_overlays is not None:
            if not isinstance(requested_overlays, dict):
                raise ValueError("overlays must be an object")
            unknown = set(requested_overlays) - VALID_OVERLAYS
            if unknown:
                raise ValueError("unsupported overlays: " + ", ".join(sorted(unknown)))
            for name, enabled in requested_overlays.items():
                if not isinstance(enabled, bool):
                    raise ValueError(f"overlay {name} must be boolean")
                next_state["overlays"][name] = enabled

        next_state["source"] = str(payload.get("source") or "manual")[:32]
        next_state["updated_at"] = int(time.time())
        next_state["revision"] = _control["revision"] + 1
        _control.clear()
        _control.update(next_state)
        _persist_control(next_state)
        return {**next_state, "timeframes": list(next_state["timeframes"]),
                "overlays": dict(next_state["overlays"])}


def _restore_control():
    if not PERSIST_STATE or not os.path.isfile(CONTROL_STATE_PATH):
        return
    try:
        with open(CONTROL_STATE_PATH, encoding="utf-8") as stream:
            saved = json.load(stream)
        patch = {key: saved[key] for key in
                 ("layout", "timeframes", "show_sidebar", "overlays")
                 if key in saved}
        patch["source"] = "restored"
        update_control(patch)
        print(f"[control] restored {CONTROL_STATE_PATH}", flush=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"[control] ignored invalid persisted state: {exc}", flush=True)


_restore_control()


def _spoken_timeframe(tf):
    return {
        "1m": "one minute", "5m": "five minute", "15m": "fifteen minute",
        "30m": "thirty minute", "1h": "one hour", "4h": "four hour",
        "1d": "daily",
    }.get(tf, str(tf))


def validate_control_announcement(payload):
    custom = payload.get("announcement")
    if custom is not None and not isinstance(custom, str):
        raise ValueError("announcement must be a string")
    if "announce" in payload and not isinstance(payload["announce"], bool):
        raise ValueError("announce must be boolean")
    if not isinstance(payload.get("requested_by", ""), str):
        raise ValueError("requested_by must be a string")
    if not isinstance(payload.get("announcement_context", ""), str):
        raise ValueError("announcement_context must be a string")


def control_announcement(payload, state):
    """Return announcement text, or None when this action should stay silent.

    Silence is the default. A custom ``announcement`` implies opt-in unless the
    caller explicitly sends ``announce: false``. This keeps scheduled/agent
    calls concise while allowing an operator to suppress any announcement.
    """
    custom = payload.get("announcement")
    enabled = payload.get("announce", bool(custom and custom.strip()))
    if not enabled:
        return None

    requested_by = payload.get("requested_by", "")
    context = payload.get("announcement_context", "")
    requested_by = requested_by.strip()[:80]
    context = context.strip()[:240]

    text = (custom or "").strip()[:500]
    if not text:
        changes = []
        if "timeframe" in payload:
            changes.append(
                f"The chart is now focused on the {_spoken_timeframe(state['timeframes'][0])} timeframe")
        elif "timeframes" in payload or "layout" in payload:
            labels = [_spoken_timeframe(tf) for tf in state["timeframes"]]
            if len(labels) > 1:
                joined = ", ".join(labels[:-1]) + f", and {labels[-1]}"
            else:
                joined = labels[0]
            changes.append(f"The chart layout now shows {joined}")
        if "show_sidebar" in payload:
            changes.append("the conversation panel is now " +
                           ("visible" if state["show_sidebar"] else "hidden"))
        if "overlays" in payload:
            pretty = {
                "market_structure": "market structure", "key_levels": "key levels",
                "premium_discount": "premium and discount zones",
                "fvg": "fair value gaps", "order_blocks": "order blocks",
                "patterns": "patterns", "liquidity": "liquidity",
            }
            for name, value in payload["overlays"].items():
                changes.append(f"{pretty.get(name, name)} is now {'on' if value else 'off'}")
        text = ". ".join(changes) or "The live display has been updated"

    if requested_by:
        text = f"{requested_by} requested this change. {text}"
    if context:
        text = f"{text}. {context}"
    return text.rstrip(". ") + "."


def add_message(who, text, kind, audio_ms=0, audio_url=None, metadata=None):
    global _next_id
    with _lock:
        msg = {"id": _next_id, "who": who, "text": text, "kind": kind,
               "audio_ms": audio_ms, "audio_url": audio_url}
        if metadata:
            msg["metadata"] = metadata
        _messages.append(msg)
        _next_id += 1
        if len(_messages) > 200:
            del _messages[:-200]
        return msg["id"]


def messages_since(since):
    with _lock:
        out = [m for m in _messages if m["id"] > since]
        last = _messages[-1]["id"] if _messages else since
        return out, last


def load_youtube_templates():
    try:
        with open(YOUTUBE_MESSAGES_PATH, encoding="utf-8") as stream:
            templates = json.load(stream)
        return templates if isinstance(templates, dict) else {}
    except Exception as exc:
        print(f"[youtube] message templates unavailable: {exc}", flush=True)
        return {}


YOUTUBE_TEMPLATES = load_youtube_templates()
SPEECH_PRIORITIES = {"critical": 0, "high": 10, "normal": 20, "low": 30}
_speech_queue = queue.PriorityQueue(maxsize=128)
_speech_sequence = itertools.count(1)
_speech_lock = threading.Lock()
_speech_dedupe_until = {}
_speech_state = {
    "queued": 0,
    "speaking": False,
    "current": None,
    "completed": 0,
    "dropped": 0,
    "last_completed_at": None,
    "gap_seconds": SPEECH_GAP_SECONDS,
}


def youtube_policy_snapshot():
    with _youtube_policy_lock:
        return dict(_youtube_policy)


def update_youtube_policy(payload):
    with _youtube_policy_lock:
        if "speak_mode" in payload:
            mode = payload["speak_mode"]
            if mode not in ("off", "selective", "all"):
                raise ValueError("speak_mode must be off, selective, or all")
            _youtube_policy["speak_mode"] = mode
        if "cooldown_seconds" in payload:
            cooldown = payload["cooldown_seconds"]
            if not isinstance(cooldown, int) or isinstance(cooldown, bool):
                raise ValueError("cooldown_seconds must be an integer")
            if cooldown < 0 or cooldown > 3600:
                raise ValueError("cooldown_seconds must be between 0 and 3600")
            _youtube_policy["cooldown_seconds"] = cooldown
        return dict(_youtube_policy)


def speech_status():
    with _speech_lock:
        return dict(_speech_state)


def queue_speech(text="", source="api", priority="normal", display_kind="voice",
                 metadata=None, viewer_message=None, dedupe_key="",
                 cooldown_seconds=0, after_speech=None):
    """Enqueue one character utterance in the shared, ordered speech queue.

    Every producer—YouTube, indicators, control changes, and manual actions—
    uses this function. Lower numeric priority runs first and sequence number
    preserves FIFO order within the same priority.
    """
    if priority not in SPEECH_PRIORITIES:
        raise ValueError("priority must be critical, high, normal, or low")
    text = " ".join(str(text).split()).strip()
    if text and len(text) > 500:
        raise ValueError("speech text must be at most 500 characters")
    if not text and not viewer_message:
        raise ValueError("speech text or viewer_message is required")
    cooldown_seconds = max(0, min(86400, int(cooldown_seconds or 0)))
    dedupe_key = str(dedupe_key or "").strip()[:160]
    if dedupe_key and cooldown_seconds == 0:
        cooldown_seconds = 30
    now = time.time()
    with _speech_lock:
        expired = [key for key, until in _speech_dedupe_until.items() if until <= now]
        for key in expired:
            del _speech_dedupe_until[key]
        if dedupe_key and _speech_dedupe_until.get(dedupe_key, 0) > now:
            _speech_state["dropped"] += 1
            return {"queued": False, "reason": "duplicate_or_cooldown"}
        if dedupe_key:
            _speech_dedupe_until[dedupe_key] = now + cooldown_seconds

    sequence = next(_speech_sequence)
    job = {
        "id": sequence,
        "text": text,
        "source": str(source or "api")[:40],
        "priority": priority,
        "display_kind": display_kind if display_kind in ("voice", "ai") else "voice",
        "metadata": metadata or {},
        "viewer_message": viewer_message,
        "after_speech": after_speech,
        "queued_at": int(now),
    }
    try:
        _speech_queue.put_nowait((SPEECH_PRIORITIES[priority], sequence, job))
        with _speech_lock:
            _speech_state["queued"] = _speech_queue.qsize()
        return {"queued": True, "id": sequence, "priority": priority}
    except queue.Full:
        with _speech_lock:
            if dedupe_key:
                _speech_dedupe_until.pop(dedupe_key, None)
            _speech_state["dropped"] += 1
        print("[speech] queue full; skipping", flush=True)
        return {"queued": False, "reason": "queue_full"}


def load_audience_rules():
    try:
        with open(AUDIENCE_RULES_PATH, encoding="utf-8") as stream:
            rules = json.load(stream)
        if not isinstance(rules, dict):
            raise ValueError("rules root must be an object")
        return rules
    except Exception as exc:
        print(f"[audience] command rules unavailable: {exc}", flush=True)
        return {}


def _restore_chart(previous, expected_revision):
    current = control_snapshot()
    if current["revision"] != expected_revision:
        print("[audience] temporary chart restore skipped after newer control change",
              flush=True)
        return
    update_control({
        "layout": previous["layout"],
        "timeframes": previous["timeframes"],
        "source": "audience-expiry",
    })


def _schedule_chart_restore(previous, applied_revision, duration_seconds):
    if not duration_seconds:
        return
    timer = threading.Timer(
        duration_seconds, _restore_chart, args=(previous, applied_revision))
    timer.daemon = True
    timer.start()


def _tool_chart_single(arguments, message):
    timeframe = arguments.get("timeframe")
    if timeframe not in VALID_TIMEFRAMES:
        raise ValueError("unsupported timeframe")
    previous = control_snapshot()
    state = update_control({"timeframe": timeframe, "source": "youtube-command"})
    duration = int(arguments.get("duration_seconds") or 0)
    _schedule_chart_restore(previous, state["revision"], duration)
    label = _spoken_timeframe(timeframe)
    return {
        "layout": "single", "timeframe": timeframe, "label": label,
        "revision": state["revision"], "temporary_seconds": duration,
    }


def _tool_chart_grid(arguments, message):
    timeframes = arguments.get("timeframes") or ["1m", "5m", "15m", "1h"]
    previous = control_snapshot()
    state = update_control({"layout": "grid", "timeframes": timeframes,
                            "source": "youtube-command"})
    duration = int(arguments.get("duration_seconds") or 0)
    _schedule_chart_restore(previous, state["revision"], duration)
    return {
        "layout": "grid", "timeframes": timeframes,
        "revision": state["revision"], "temporary_seconds": duration,
    }


def _tool_music_next(_arguments, _message):
    return music_request("/next", {})


def _tool_music_play(arguments, _message):
    query = " ".join(str(arguments.get("query") or "").split()).strip()
    if not query:
        raise ValueError("song title is required")
    return music_request("/play", {"query": query})


def _audience_agent_router(text, tools):
    if AUDIENCE_COMMAND_ROUTER not in ("ai", "hybrid"):
        return None
    return ai.choose_audience_tool(text, tools)


_audience_engine = audience_commands.AudienceCommandEngine(
    tools={
        "chart.set_single": _tool_chart_single,
        "chart.set_grid": _tool_chart_grid,
        "music.next": _tool_music_next,
        "music.play": _tool_music_play,
    },
    rules=load_audience_rules(),
    router=_audience_agent_router,
)
_audience_queue = queue.Queue(maxsize=128)


def _queue_regular_youtube_reply(message):
    if youtube_policy_snapshot()["speak_mode"] == "off":
        return
    priority = "high" if message.get("event_type") == "superChatEvent" else "normal"
    queue_speech(source="youtube-chat", priority=priority,
                 viewer_message=message, display_kind="voice",
                 metadata={"platform": "youtube"},
                 dedupe_key=f"youtube:{message.get('id', '')}",
                 cooldown_seconds=86400)


def _audience_command_announcement(decision, message):
    tool = decision.get("tool")
    arguments = decision.get("arguments", {})
    author = message.get("author", "A viewer")
    duration = int(arguments.get("duration_seconds") or 0)
    duration_text = f" for {duration} seconds" if duration else ""
    if tool == "chart.set_single":
        label = _spoken_timeframe(arguments.get("timeframe"))
        return f"{author} asked for the {label} chart. I will switch it now{duration_text}."
    if tool == "chart.set_grid":
        return f"{author} asked for the multi-timeframe view. I will switch it now."
    if tool == "music.next":
        return f"{author} asked for the next track. I will change it now."
    if tool == "music.play":
        title = str(arguments.get("query") or "that track")[:80]
        return f"{author} asked for {title}. I will play it now."
    return f"{author} requested a stream change. I will apply it now."


def on_youtube_message(message):
    """Record a viewer comment and route it through the command agent."""
    add_message(message["author"], message["text"], "viewer", metadata={
        "platform": "youtube", "youtube_message_id": message.get("id", ""),
        "is_moderator": message.get("is_moderator", False),
        "is_sponsor": message.get("is_sponsor", False),
        "event_type": message.get("event_type", "textMessageEvent"),
        "purchase_amount": message.get("purchase_amount"),
    })
    try:
        _audience_queue.put_nowait(message)
    except queue.Full:
        print("[audience] command queue full; message not evaluated", flush=True)


def _audience_worker():
    while True:
        message = _audience_queue.get()
        try:
            decision = _audience_engine.process(message, defer_execute=True)
            if not decision.get("matched"):
                _queue_regular_youtube_reply(message)
                continue
            if decision.get("approved"):
                if decision.get("announce_result"):
                    announcement = _audience_command_announcement(decision, message)

                    def execute_after_speech(_playback, approved=decision,
                                             original=message):
                        result = _audience_engine.execute_authorized(approved, original)
                        if not result.get("executed"):
                            print(f"[audience] deferred command failed: {result}", flush=True)

                    queued = queue_speech(
                        announcement, source="audience-command", priority="high",
                        display_kind="voice",
                        dedupe_key=f"command:{message.get('id', '')}",
                        cooldown_seconds=86400, after_speech=execute_after_speech,
                        metadata={"tool": decision.get("tool"),
                                  "youtube_message_id": message.get("id", "")})
                    if not queued.get("queued"):
                        print(f"[audience] command not executed because speech was not queued: {queued}",
                              flush=True)
                else:
                    _audience_engine.execute_authorized(decision, message)
                continue
            reason = decision.get("reason")
            if reason in ("disabled", "approval_required"):
                queue_speech("That control is not available to chat yet.",
                             source="audience-command", priority="normal")
            elif reason in ("role_not_allowed", "superchat_required",
                            "minimum_superchat_not_met"):
                queue_speech("That control is currently reserved for eligible supporters or moderators.",
                             source="audience-command", priority="normal")
            elif reason == "execution_failed":
                queue_speech("I could not apply that stream change right now.",
                             source="audience-command", priority="normal")
            # Cooldown denials stay silent to avoid amplifying repeated spam.
        except Exception as exc:
            print(f"[audience] command processing failed: {exc}", flush=True)
            _queue_regular_youtube_reply(message)
        finally:
            _audience_queue.task_done()


threading.Thread(target=_audience_worker, daemon=True,
                 name="audience-commands").start()


def _speech_worker():
    """Resolve, synthesize, play, and space every character utterance."""
    last_youtube_spoken = 0
    while True:
        _priority, _sequence, item = _speech_queue.get()
        played = False
        try:
            with _speech_lock:
                _speech_state["queued"] = _speech_queue.qsize()
                _speech_state["speaking"] = True
                _speech_state["current"] = {
                    "id": item["id"], "source": item["source"],
                    "priority": item["priority"], "queued_at": item["queued_at"],
                }
            message = item.get("viewer_message")
            if message:
                policy = youtube_policy_snapshot()
                if policy["speak_mode"] == "off":
                    continue
                if time.time() - last_youtube_spoken < policy["cooldown_seconds"]:
                    continue
                if policy["speak_mode"] == "all":
                    reply, source = ai.generate(message["text"])
                    should_speak = True
                else:
                    should_speak, reply, source = ai.choose_live_reply(
                        message["text"], message["author"])
                metadata = {
                    "platform": "youtube", "reply_to": message.get("id", ""),
                    "decision_source": source,
                }
            else:
                should_speak, reply = True, item["text"]
                metadata = item.get("metadata", {})
            if should_speak and reply:
                path, url, ms = synthesize(reply)
                if path:
                    metadata = {**metadata, "speech_id": item["id"],
                                "speech_source": item["source"],
                                "priority": item["priority"]}
                    add_message("AI host", reply, item["display_kind"],
                                audio_ms=ms, audio_url=url, metadata=metadata)
                    # Give the browser poll/lipsync path a small head start.
                    if SPEECH_LIPSYNC_LEAD_SECONDS:
                        time.sleep(SPEECH_LIPSYNC_LEAD_SECONDS)
                    played = play_audio(path)
                    if played and message and _youtube_bridge:
                        _youtube_bridge.mark_spoken()
                        last_youtube_spoken = time.time()
            callback = item.get("after_speech")
            if callback:
                callback({"played": played, "speech_id": item["id"]})
        except Exception as exc:
            print(f"[speech] request failed: {exc}", flush=True)
        finally:
            _speech_queue.task_done()
            with _speech_lock:
                _speech_state["queued"] = _speech_queue.qsize()
                _speech_state["speaking"] = False
                _speech_state["current"] = None
                if played:
                    _speech_state["completed"] += 1
                    _speech_state["last_completed_at"] = int(time.time())
        if played and SPEECH_GAP_SECONDS:
            time.sleep(SPEECH_GAP_SECONDS)


threading.Thread(target=_speech_worker, daemon=True,
                 name="speech-coordinator").start()


def queue_control_announcement(text):
    result = queue_speech(text, source="control", priority="normal",
                          display_kind="ai")
    return result["queued"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _control_authorized(self):
        if not CONTROL_TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {CONTROL_TOKEN}"

    def _external_action_authorized(self):
        """Protect YouTube writes even when the general control token is unset.

        Local workers/operators inside this container may act over loopback.
        Any request arriving through the published container port requires an
        explicitly configured CONTROL_TOKEN.
        """
        if CONTROL_TOKEN:
            return self._control_authorized()
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/health":
            youtube = _youtube_bridge.status() if _youtube_bridge else {"configured": False}
            try:
                music = music_request("/status")
                music_ok = bool(music.get("track"))
            except (ValueError, RuntimeError):
                music_ok = False
            return self._json(200, {
                "status": "ok" if music_ok else "degraded",
                "scene": True,
                "music": music_ok,
                "youtube_configured": bool(youtube.get("configured")),
                "youtube_connected": bool(youtube.get("connected")),
            })

        if parsed.path == "/api/runtime-config":
            return self._json(200, {
                "ohlc_ws_url": OHLC_WS_URL,
                "ohlc_symbol": OHLC_SYMBOL,
            })

        if parsed.path == "/api/control":
            return self._json(200, control_snapshot())

        if parsed.path == "/api/youtube/status":
            status = _youtube_bridge.status() if _youtube_bridge else {
                "configured": False, "connected": False,
                "last_error": "YouTube bridge has not started",
            }
            return self._json(200, {
                **status,
                **youtube_policy_snapshot(),
                "templates": sorted(YOUTUBE_TEMPLATES),
            })

        if parsed.path == "/api/speech/status":
            return self._json(200, speech_status())

        if parsed.path == "/api/audience/status":
            return self._json(200, {
                **_audience_engine.status(),
                "queued": _audience_queue.qsize(),
                "router": AUDIENCE_COMMAND_ROUTER,
            })

        if parsed.path in ("/api/music/status", "/api/music/catalog"):
            try:
                suffix = "/status" if parsed.path.endswith("/status") else "/catalog"
                return self._json(200, music_request(suffix))
            except (ValueError, RuntimeError) as exc:
                return self._json(503, {"error": str(exc)})

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

        # static files
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
        if path not in ("/api/messages", "/api/ask", "/api/control",
                        "/api/youtube/publish", "/api/youtube/policy",
                "/api/youtube/mock-message",
                        "/api/voice", "/api/speech/trigger",
                        "/api/audience/policy", "/api/music/play",
                        "/api/music/next", "/api/music/previous",
                        "/api/music/pause", "/api/music/resume",
                        "/api/music/volume"):
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})

        if path == "/api/control":
            if not self._control_authorized():
                return self._json(401, {"error": "unauthorized"})
            try:
                # Validate announcement fields before committing the visual
                # state, then synthesize asynchronously after the update.
                validate_control_announcement(payload)
                state = update_control(payload)
                announcement = control_announcement(payload, state)
                queued = False
                if announcement:
                    queued = queue_control_announcement(announcement)
                return self._json(200, {
                    **state,
                    "announcement": {
                        "queued": queued,
                        "text": announcement,
                    },
                })
            except ValueError as e:
                return self._json(400, {"error": str(e)})

        if path == "/api/youtube/publish":
            if not self._external_action_authorized():
                return self._json(401, {"error": "unauthorized"})
            template_name = str(payload.get("template") or "").strip()
            template = YOUTUBE_TEMPLATES.get(template_name, {}) if template_name else {}
            text = str(payload.get("text") or template.get("text") or "").strip()
            category = payload.get("category") or template.get("category") or "manual"
            try:
                if not _youtube_bridge:
                    raise youtube_chat.YouTubeError("YouTube bridge has not started")
                queued = _youtube_bridge.publish(
                    text, category=category, source=payload.get("source", "api"))
                return self._json(202, {"accepted": True, "message": queued})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except youtube_chat.YouTubeError as exc:
                return self._json(503, {"error": str(exc)})

        if path == "/api/youtube/policy":
            if not self._external_action_authorized():
                return self._json(401, {"error": "unauthorized"})
            try:
                return self._json(200, update_youtube_policy(payload))
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})

        if path == "/api/youtube/mock-message":
            if not self._external_action_authorized():
                return self._json(401, {"error": "unauthorized"})
            text = " ".join(str(payload.get("text") or "").split()).strip()
            if not text:
                return self._json(400, {"error": "text is required"})
            if not _youtube_bridge or _youtube_bridge.status().get("transport") != "mock":
                return self._json(409, {"error": "mock YouTube chat is not enabled"})
            try:
                item = _youtube_bridge.client.inject(
                    text,
                    author=str(payload.get("author") or "Mock viewer")[:80],
                    is_moderator=bool(payload.get("is_moderator", False)),
                    is_sponsor=bool(payload.get("is_sponsor", False)),
                    event_type=str(payload.get("event_type") or "textMessageEvent"),
                    amount_micros=int(payload.get("amount_micros") or 0),
                    purchase_amount=str(payload.get("purchase_amount") or ""),
                    tier=int(payload.get("tier") or 0),
                )
                return self._json(202, {"accepted": True, "id": item["id"]})
            except (TypeError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})

        if path == "/api/audience/policy":
            if not self._external_action_authorized():
                return self._json(401, {"error": "unauthorized"})
            tool_name = payload.get("tool")
            rule_patch = payload.get("rule")
            if not isinstance(tool_name, str) or not isinstance(rule_patch, dict):
                return self._json(400, {"error": "tool and rule object are required"})
            try:
                rule = _audience_engine.update_rule(tool_name, rule_patch)
                return self._json(200, {"tool": tool_name, "rule": rule})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})

        if path.startswith("/api/music/"):
            if not self._external_action_authorized():
                return self._json(401, {"error": "unauthorized"})
            action = path.removeprefix("/api/music/")
            try:
                return self._json(200, music_request("/" + action, payload))
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self._json(503, {"error": str(exc)})

        if path in ("/api/voice", "/api/speech/trigger"):
            if not self._external_action_authorized():
                return self._json(401, {"error": "unauthorized"})
            text = " ".join(str(payload.get("text") or "").split()).strip()
            if not text:
                return self._json(400, {"error": "text is required"})
            source = str(payload.get("source") or
                         ("manual" if path == "/api/voice" else "notifier"))[:40]
            default_priority = "high" if source in ("indicator", "superchat") else "normal"
            priority = payload.get("priority", default_priority)
            show_text = payload.get("show_text", False)
            if not isinstance(show_text, bool):
                return self._json(400, {"error": "show_text must be boolean"})
            cooldown = payload.get("cooldown_seconds", 0)
            if not isinstance(cooldown, int) or isinstance(cooldown, bool):
                return self._json(400, {"error": "cooldown_seconds must be an integer"})
            try:
                result = queue_speech(
                    text, source=source, priority=priority,
                    display_kind="ai" if show_text else "voice",
                    metadata={"source": source, "platform": "internal",
                              "context": payload.get("context")},
                    dedupe_key=payload.get("dedupe_key", ""),
                    cooldown_seconds=cooldown)
                return self._json(202 if result["queued"] else 200, result)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})

        if path == "/api/messages":
            mid = add_message(payload.get("who", "?"), payload.get("text", ""),
                              payload.get("kind", "viewer"))
            return self._json(200, {"id": mid})

        # /api/ask
        who = (payload.get("who") or "you").strip()[:24]
        text = (payload.get("text") or "").strip()[:400]
        if not text:
            return self._json(400, {"error": "empty question"})
        add_message(who, text, "viewer")
        reply, source = ai.generate(text)
        speech = queue_speech(
            reply, source="direct-question", priority="high", display_kind="ai",
            metadata={"question_from": who, "decision_source": source})
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} | {who}: {text} | "
                        f"AI({source}): {reply}\n")
        except Exception:
            pass
        self._json(200, {"reply": reply, "source": source, "speech": speech})


if __name__ == "__main__":
    _youtube_bridge = youtube_chat.bridge_from_env(on_youtube_message)
    started = _youtube_bridge.start()
    print(f"[youtube] configured={_youtube_bridge.status()['configured']} "
          f"started={started} speak_mode={youtube_policy_snapshot()['speak_mode']}", flush=True)
    port = int(os.environ.get("SCENE_PORT", "8080"))
    add_message("AI host", "Welcome to the Night Shift. Pull up a chair and ask me anything.", "ai")
    print(f"[scene_server] listening on :{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
