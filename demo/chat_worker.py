#!/usr/bin/env python3
"""Simulates background live-chat viewers + AI host replies.

Interactive questions you type yourself go through scene_server's /api/ask;
this worker just adds ambient chatter so the stream isn't empty. Text only
(no audio in this demo); production speaks the reply via TTS.
"""
import json, os, time, urllib.request, itertools, random
import ai

SCENE = os.environ.get("SCENE_BASE", "http://127.0.0.1:8080")
LOG_PATH = os.path.join(os.path.dirname(__file__), "logs", "replies.log")

VIEWERS = [
    ("trader_92", "is BTC looking bullish right now?"),
    ("swing_queen", "what timeframe are you watching?"),
    ("moon_kid", "is the chart live or delayed?"),
    ("anita.k", "which indicators do you trust most?"),
    ("deltaOne", "thoughts on the RSI divergence here?"),
    ("pip_hunter", "are you scalping or swinging today?"),
    ("late2crypto", "just joined, what are we looking at?"),
    ("vol_watch", "volume looks thin, no?"),
    ("gg_gary", "do you use stop losses on these setups?"),
    ("nari_fx", "EUR/USD next move?"),
]


def post(who, text, kind):
    data = json.dumps({"who": who, "text": text, "kind": kind}).encode()
    req = urllib.request.Request(SCENE + "/api/messages", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        print(f"[worker] post failed: {e}")


def log(viewer, vtext, reply, source):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} | {viewer}: {vtext} | "
                f"AI({source}): {reply}\n")


def main():
    time.sleep(3)
    post("AI host", "Stream's live! Type a message and I'll answer.", "ai")
    for viewer, vtext in itertools.cycle(VIEWERS):
        post(viewer, vtext, "viewer")
        time.sleep(1.2)
        reply, source = ai.generate(vtext)
        post("AI host", reply, "ai")
        log(viewer, vtext, reply, source)
        print(f"[worker] {source}: {reply}", flush=True)
        time.sleep(random.uniform(14, 20))


if __name__ == "__main__":
    main()
