"""Shared overlay state, mutated by control.py, read by app.py's Cairo draw callback.

Same shape/contract as the Smelter version's state.ts, so the two are a fair
comparison: same control API, different compositor underneath.
"""
import threading
import time

_lock = threading.Lock()

state = {
    "layout": "main",       # main | logs | poll | leaderboard
    "logs": [],              # [{ts, text}]
    "poll": None,            # {question, options: [{label, votes}]}
    "leaderboard": [],       # [{name, score}]
}


def set_layout(layout):
    with _lock:
        state["layout"] = layout


def push_log(text):
    with _lock:
        state["logs"].append({"ts": time.strftime("%H:%M:%S"), "text": text})
        if len(state["logs"]) > 8:
            state["logs"].pop(0)


def set_poll(question, option_labels):
    with _lock:
        state["poll"] = {"question": question, "options": [{"label": l, "votes": 0} for l in option_labels]}


def vote_poll(option_label):
    with _lock:
        if not state["poll"]:
            return
        for o in state["poll"]["options"]:
            if o["label"] == option_label:
                o["votes"] += 1


def set_leaderboard(entries):
    with _lock:
        state["leaderboard"] = sorted(entries, key=lambda e: -e["score"])[:8]


def snapshot():
    with _lock:
        # shallow copy is enough; we only ever replace sub-structures wholesale
        return dict(state)
