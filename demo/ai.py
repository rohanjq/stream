"""Shared DeepSeek-Flash caller (used by scene_server and chat_worker)."""
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
