"""Shared DeepSeek-Flash caller used by the live scene server."""
import json, os, urllib.request, urllib.error, itertools

AI_BASE = os.environ.get("DEEPSEEK_URL", "").rstrip("/")
AI_KEY = os.environ.get("DEEPSEEK_KEY", "")
AI_MODEL = os.environ.get("DEEPSEEK_MODEL", "DeepSeek-V4-Flash")

SYSTEM = (
    "You are an upbeat live-stream host for a trading/charting channel. "
    "A viewer in live chat said something. Reply in ONE short, casual sentence "
    "(max ~25 words). No markdown, no emojis, no preamble."
)

_FALLBACK = itertools.cycle([
    "Good question! I'm watching the higher timeframe before I commit either way.",
    "Right now I'd stay patient and let the candle close before reacting.",
    "The chart's live here in the stream — no delay on my end.",
    "I lean on volume plus a simple moving average; keep it clean.",
    "That's interesting, but I'd want confirmation before acting.",
    "Always use a stop — protecting capital beats being right.",
])


def generate(viewer_text):
    """Return (reply, source). source is 'deepseek' or 'fallback'."""
    if not (AI_BASE and AI_KEY):
        return next(_FALLBACK), "fallback"
    body = json.dumps({
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": viewer_text},
        ],
        "max_tokens": 80,
        "temperature": 0.8,
    }).encode()
    url = AI_BASE + "/chat/completions"
    for headers in ({"Authorization": f"Bearer {AI_KEY}"}, {"api-key": AI_KEY}):
        headers["Content-Type"] = "application/json"
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            resp = urllib.request.urlopen(req, timeout=20).read()
            reply = json.loads(resp)["choices"][0]["message"]["content"].strip()
            if reply:
                return reply, "deepseek"
        except urllib.error.HTTPError as e:
            print(f"[ai] HTTP {e.code}: {e.read()[:160]!r}")
        except Exception as e:
            print(f"[ai] error: {e}")
    return next(_FALLBACK), "fallback"


def choose_live_reply(viewer_text, author="viewer"):
    """Decide whether a YouTube comment merits an audio-only host reply.

    Returns ``(should_speak, reply, source)``. The fallback is intentionally
    conservative: only direct questions are spoken when the model is absent.
    """
    text = " ".join(str(viewer_text).split()).strip()[:500]
    if not text:
        return False, "", "empty"

    def fallback():
        lower = text.lower()
        spam = ("http://" in lower or "https://" in lower or
                lower.count("!") > 4 or len(text) < 5)
        question = "?" in text or lower.startswith((
            "what ", "why ", "how ", "when ", "where ", "which ",
            "can ", "could ", "would ", "is ", "are ", "do ", "does "))
        if spam or not question:
            return False, "", "heuristic"
        reply, source = generate(text)
        return True, reply, source

    if not (AI_BASE and AI_KEY):
        return fallback()
    decision_system = (
        "You select comments for an audio-only host on a live market-chart stream. "
        "Speak only when a concise answer helps the room: direct questions about the "
        "visible chart, stream controls, market education, or how the stream works. "
        "Skip greetings, emojis, spam, promotions, repeated comments, demands for "
        "guaranteed trades, and casual statements. Never claim certainty or give "
        "personalized financial advice. Return JSON only as "
        '{"speak":true|false,"reply":"one short sentence, max 25 words"}.')
    body = json.dumps({
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": decision_system},
            {"role": "user", "content": f"{author}: {text}"},
        ],
        "max_tokens": 100,
        "temperature": 0.4,
    }).encode()
    url = AI_BASE + "/chat/completions"
    for headers in ({"Authorization": f"Bearer {AI_KEY}"}, {"api-key": AI_KEY}):
        headers["Content-Type"] = "application/json"
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            raw = json.loads(urllib.request.urlopen(req, timeout=20).read())
            content = raw["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.strip("`").removeprefix("json").strip()
            decision = json.loads(content)
            should_speak = decision.get("speak") is True
            reply = " ".join(str(decision.get("reply", "")).split())[:300]
            if should_speak and reply:
                return True, reply, "deepseek-selective"
            return False, "", "deepseek-selective"
        except Exception as e:
            print(f"[ai] selection error: {e}")
    return fallback()


def choose_audience_tool(viewer_text, allowed_tools):
    """Return a constrained control-center tool proposal or ``None``.

    Known commands are parsed deterministically before this is called. This
    router exists for future natural-language variations and may only select a
    name supplied by the command registry.
    """
    if not (AI_BASE and AI_KEY and allowed_tools):
        return None
    tool_help = {
        "chart.set_single": "show one chart; args: timeframe (1m,5m,15m,30m,1h), optional duration_seconds",
        "chart.set_grid": "show the standard multi-timeframe grid; optional duration_seconds",
        "music.next": "skip to the next music track; no args",
        "music.play": "play a catalog track; args: query containing its title",
    }
    catalog = "; ".join(f"{name}: {tool_help.get(name, name)}" for name in allowed_tools)
    system = (
        "You route explicit viewer requests to a tiny control-center tool list. "
        "Never infer a command from market discussion or a statement. Only act "
        "when the viewer clearly asks the stream to change something. Return JSON "
        "only: null when there is no command, otherwise "
        '{"tool":"exact.name","arguments":{}}. Available tools: ' + catalog)
    body = json.dumps({
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": str(viewer_text)[:500]}],
        "max_tokens": 100,
        "temperature": 0.0,
    }).encode()
    url = AI_BASE + "/chat/completions"
    for headers in ({"Authorization": f"Bearer {AI_KEY}"}, {"api-key": AI_KEY}):
        headers["Content-Type"] = "application/json"
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            raw = json.loads(urllib.request.urlopen(req, timeout=20).read())
            content = raw["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.strip("`").removeprefix("json").strip()
            proposal = json.loads(content)
            if not isinstance(proposal, dict) or proposal.get("tool") not in allowed_tools:
                return None
            arguments = proposal.get("arguments")
            if not isinstance(arguments, dict):
                return None
            return {"tool": proposal["tool"], "arguments": arguments,
                    "confidence": 0.8, "parser": "agent"}
        except Exception as exc:
            print(f"[ai] command routing error: {exc}")
    return None
