#!/usr/bin/env python3
"""Policy-gated audience command routing for live-chat messages."""
from collections import deque
import copy
import re
import threading
import time


TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h")
COMMAND_WORDS = re.compile(
    r"\b(change|switch|set|show|hide|use|put|open|next|skip|play)\b", re.I)
TIMEFRAME = re.compile(r"\b(30m|15m|5m|1m|1h)\b", re.I)
DURATION = re.compile(r"\bfor\s+(\d{1,3})\s*(seconds?|secs?|minutes?|mins?)\b", re.I)


def parse_builtin(text):
    """Return a conservative tool proposal for known audience phrases."""
    clean = " ".join(str(text).split()).strip()
    if not clean or not COMMAND_WORDS.search(clean):
        return None
    lower = clean.lower()
    duration = 0
    duration_match = DURATION.search(lower)
    if duration_match:
        duration = int(duration_match.group(1))
        if duration_match.group(2).startswith("min"):
            duration *= 60

    tf_match = TIMEFRAME.search(lower)
    if tf_match and any(word in lower for word in ("chart", "view", "timeframe", "time frame")):
        return {
            "tool": "chart.set_single",
            "arguments": {"timeframe": tf_match.group(1).lower(),
                          "duration_seconds": duration},
            "confidence": 1.0,
            "parser": "rules",
        }
    if ("multi" in lower or "four" in lower) and any(
            word in lower for word in ("chart", "view", "timeframe", "time frame")):
        return {
            "tool": "chart.set_grid",
            "arguments": {"timeframes": ["1m", "5m", "15m", "1h"],
                          "duration_seconds": duration},
            "confidence": 1.0,
            "parser": "rules",
        }
    if (("next" in lower or "skip" in lower) and
            any(word in lower for word in ("song", "track", "music"))):
        return {"tool": "music.next", "arguments": {}, "confidence": 0.95,
                "parser": "rules"}
    play_match = re.search(
        r"\b(?:play|change(?:\s+the)?\s+(?:song|music|track)\s+to)\s+(.+?)(?:\s+please)?$",
        clean, re.I)
    if play_match:
        query = play_match.group(1).strip(" .!?\"")
        if query and query.lower() not in ("a song", "some music", "music"):
            return {"tool": "music.play", "arguments": {"query": query},
                    "confidence": 0.98, "parser": "rules"}
    return None


class AudienceCommandEngine:
    """Resolve, authorize, rate-limit, execute, and audit audience tools."""
    def __init__(self, tools, rules, router=None, audit_size=200):
        self.tools = dict(tools)
        self.rules = copy.deepcopy(rules)
        self.router = router
        self._lock = threading.Lock()
        self._global_last = {}
        self._user_last = {}
        self._audit = deque(maxlen=audit_size)
        self._counts = {"considered": 0, "matched": 0, "executed": 0,
                        "approved": 0, "denied": 0, "failed": 0}

    def status(self):
        with self._lock:
            return {"counts": dict(self._counts), "rules": copy.deepcopy(self.rules),
                    "recent": list(self._audit)[-20:]}

    def update_rule(self, tool_name, patch):
        if tool_name not in self.rules:
            raise ValueError("unknown audience tool")
        allowed = {"enabled", "auto_execute", "roles", "require_superchat",
                   "min_superchat_micros", "global_cooldown_seconds",
                   "user_cooldown_seconds", "default_duration_seconds",
                   "max_duration_seconds", "announce_result"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError("unsupported rule fields: " + ", ".join(sorted(unknown)))
        with self._lock:
            candidate = copy.deepcopy(self.rules[tool_name])
            candidate.update(copy.deepcopy(patch))
            for field in ("enabled", "auto_execute", "require_superchat",
                          "announce_result"):
                if not isinstance(candidate.get(field), bool):
                    raise ValueError(f"{field} must be boolean")
            roles = candidate.get("roles")
            valid_roles = {"viewer", "sponsor", "superchat", "moderator", "owner"}
            if (not isinstance(roles, list) or not roles or
                    any(role not in valid_roles for role in roles)):
                raise ValueError("roles contains an unsupported role")
            for field in ("min_superchat_micros", "global_cooldown_seconds",
                          "user_cooldown_seconds", "default_duration_seconds",
                          "max_duration_seconds"):
                value = candidate.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{field} must be a non-negative integer")
            self.rules[tool_name] = candidate
            return copy.deepcopy(self.rules[tool_name])

    def _role(self, message):
        if message.get("is_owner"):
            return "owner"
        if message.get("is_moderator"):
            return "moderator"
        if message.get("event_type") == "superChatEvent":
            return "superchat"
        if message.get("is_sponsor"):
            return "sponsor"
        return "viewer"

    def _record(self, decision):
        safe = {key: value for key, value in decision.items()
                if key not in ("channel_id",)}
        with self._lock:
            self._audit.append(safe)

    def execute_authorized(self, decision, message):
        """Execute a decision that already passed policy and cooldown checks."""
        completed = copy.deepcopy(decision)
        try:
            result = self.tools[decision["tool"]](decision["arguments"], message)
            completed.update({"executed": True, "result": result,
                              "completed_at": int(time.time())})
            with self._lock:
                self._counts["executed"] += 1
        except Exception as exc:
            completed["reason"] = "execution_failed"
            completed["error"] = str(exc)[:200]
            with self._lock:
                self._counts["failed"] += 1
        self._record(completed)
        return completed

    def process(self, message, defer_execute=False):
        now = time.time()
        with self._lock:
            self._counts["considered"] += 1
        proposal = parse_builtin(message.get("text", ""))
        if not proposal and self.router and COMMAND_WORDS.search(message.get("text", "")):
            proposal = self.router(message.get("text", ""), sorted(self.tools))
        if not proposal:
            return {"matched": False, "executed": False}

        tool_name = proposal.get("tool")
        rule = copy.deepcopy(self.rules.get(tool_name))
        role = self._role(message)
        decision = {
            "at": int(now), "message_id": message.get("id", ""),
            "author": message.get("author", "")[:80], "role": role,
            "tool": tool_name, "arguments": proposal.get("arguments", {}),
            "parser": proposal.get("parser", "unknown"),
            "matched": True, "executed": False,
        }
        with self._lock:
            self._counts["matched"] += 1

        deny = None
        if not rule or tool_name not in self.tools:
            deny = "unknown_tool"
        elif not rule.get("enabled", False):
            deny = "disabled"
        elif not rule.get("auto_execute", False):
            deny = "approval_required"
        elif role not in rule.get("roles", []):
            deny = "role_not_allowed"
        elif rule.get("require_superchat") and role != "superchat":
            deny = "superchat_required"
        elif int(message.get("amount_micros") or 0) < int(rule.get("min_superchat_micros", 0)):
            deny = "minimum_superchat_not_met"

        user_key = (tool_name, message.get("channel_id") or message.get("author", ""))
        with self._lock:
            global_wait = float(rule.get("global_cooldown_seconds", 0)) if rule else 0
            user_wait = float(rule.get("user_cooldown_seconds", 0)) if rule else 0
            if not deny and now - self._global_last.get(tool_name, 0) < global_wait:
                deny = "global_cooldown"
            if not deny and now - self._user_last.get(user_key, 0) < user_wait:
                deny = "user_cooldown"

        if deny:
            decision["reason"] = deny
            with self._lock:
                self._counts["denied"] += 1
            self._record(decision)
            return decision

        arguments = dict(proposal.get("arguments") or {})
        duration = int(arguments.get("duration_seconds") or
                       rule.get("default_duration_seconds", 0))
        maximum = int(rule.get("max_duration_seconds", 0))
        if maximum:
            duration = min(duration, maximum)
        arguments["duration_seconds"] = max(0, duration)
        decision.update({"arguments": arguments, "approved": True,
                         "announce_result": bool(rule.get("announce_result", True))})
        with self._lock:
            self._global_last[tool_name] = now
            self._user_last[user_key] = now
            self._counts["approved"] += 1
        if defer_execute:
            self._record(decision)
            return decision
        return self.execute_authorized(decision, message)
