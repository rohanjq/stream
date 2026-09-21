#!/usr/bin/env python3
"""Durable, context-scoped orchestration for the live AI host.

The runtime deliberately keeps scheduling and persistence outside the model.
LangGraph orchestrates bounded workflow nodes when installed; the small local
runner preserves identical semantics for host-side tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import threading
import time

try:
    import websocket
except ImportError:  # Unit tests can exercise persistence without transport.
    websocket = None

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # Host-side tests do not install container dependencies.
    END = None
    StateGraph = None


def utc_day(timestamp=None):
    return datetime.fromtimestamp(timestamp or time.time(), timezone.utc).date().isoformat()


def clean_text(value, limit=500):
    return " ".join(str(value or "").split()).strip()[:limit]


def stable_id(*parts):
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()


def parse_time(value):
    """Parse an upstream RFC3339 timestamp without trusting local receipt time."""
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def verified_market_text(text, exact_price):
    """Reject generated numeric claims beyond the injected exact price."""
    candidate = clean_text(text, 400)
    if exact_price not in candidate:
        return False
    # The initial recurring job is intentionally narrower than the future
    # indicator/event jobs. Those need their own structured claim validators.
    forbidden = re.compile(
        r"\b(?:ema|moving averages?|rsi|macd|support|resistance|target|"
        r"forecast|prediction|bullish|bearish)\b", re.IGNORECASE)
    if forbidden.search(candidate):
        return False
    numbers = re.findall(r"(?<![A-Za-z])[+-]?\d[\d,]*(?:\.\d+)?%?", candidate)
    return bool(numbers) and all(number == exact_price for number in numbers)


class AgentStore:
    """SQLite-backed job/event/memory store for the single active host.

    WAL mode, transactional leases and unique idempotency keys make process
    restarts safe. The API is intentionally narrow so PostgreSQL can replace
    this implementation when the agent is horizontally scaled.
    """

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._initialize()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _initialize(self):
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_events (
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    person_id TEXT,
                    payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (source, source_id)
                );
                CREATE TABLE IF NOT EXISTS agent_jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    context_scope TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    available_at INTEGER NOT NULL,
                    lease_until INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    result TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_jobs_claim_idx
                    ON agent_jobs(status, available_at, lease_until);
                CREATE TABLE IF NOT EXISTS agent_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    person_id TEXT,
                    role TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_turns_thread_idx
                    ON agent_turns(thread_id, id DESC);
                CREATE TABLE IF NOT EXISTS agent_people (
                    person_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    comment_count INTEGER NOT NULL DEFAULT 0,
                    memory_active INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    summary_version INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_summaries (
                    thread_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    through_turn_id INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    trace TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
            """)

    def enqueue(self, job_id, kind, payload, context_scope="none", available_at=None):
        now = int(time.time())
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO agent_jobs
                (job_id,kind,context_scope,payload,status,available_at,created_at,updated_at)
                VALUES (?,?,?,?, 'pending', ?,?,?)
            """, (job_id, kind, context_scope, json.dumps(payload, separators=(",", ":")),
                  int(available_at or now), now, now))
            return cursor.rowcount == 1

    def claim(self, lease_seconds=90):
        now = int(time.time())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT * FROM agent_jobs
                WHERE available_at <= ? AND (
                    status='pending' OR (status='running' AND lease_until < ?)
                ) ORDER BY available_at, created_at LIMIT 1
            """, (now, now)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute("""
                UPDATE agent_jobs SET status='running', lease_until=?,
                    attempts=attempts+1, updated_at=? WHERE job_id=?
            """, (now + lease_seconds, now, row["job_id"]))
            conn.execute("COMMIT")
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            result["attempts"] += 1
            return result

    def finish(self, job_id, status, result=None, error=None, retry_at=None):
        now = int(time.time())
        if retry_at is not None:
            status = "pending"
        with self.connect() as conn:
            conn.execute("""
                UPDATE agent_jobs SET status=?, result=?, last_error=?,
                    available_at=COALESCE(?,available_at), lease_until=NULL,
                    updated_at=? WHERE job_id=?
            """, (status, json.dumps(result or {}, separators=(",", ":")),
                  clean_text(error, 500) or None, retry_at, now, job_id))

    def record_run(self, job_id, trace):
        run_id = stable_id(job_id, time.time_ns())
        with self.connect() as conn:
            conn.execute("INSERT INTO agent_runs VALUES (?,?,?,?)",
                         (run_id, job_id, json.dumps(trace, separators=(",", ":")),
                          int(time.time())))

    def record_event(self, source, source_id, kind, payload, person_id=None):
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO agent_events
                (source,source_id,kind,person_id,payload,created_at)
                VALUES (?,?,?,?,?,?)
            """, (source, source_id, kind, person_id,
                  json.dumps(payload, separators=(",", ":")), int(time.time())))
            return cursor.rowcount == 1

    def add_turn(self, thread_id, role, kind, text, person_id=None, metadata=None):
        with self.connect() as conn:
            cursor = conn.execute("""
                INSERT INTO agent_turns
                (thread_id,person_id,role,kind,text,metadata,created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (thread_id, person_id, role, kind, clean_text(text, 1000),
                  json.dumps(metadata or {}, separators=(",", ":")), int(time.time())))
            return cursor.lastrowid

    def recent_turns(self, thread_id, limit=20, after_id=0):
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT id,person_id,role,kind,text,metadata,created_at
                FROM agent_turns WHERE thread_id=? AND id>?
                ORDER BY id DESC LIMIT ?
            """, (thread_id, after_id, limit)).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            result.append(item)
        return result

    def observe_viewer_message(self, source_id, person_id, display_name, text,
                               payload, memory_threshold=10):
        """Atomically dedupe and persist a viewer event, profile and both turns."""
        now = int(time.time())
        day_thread = f"broadcast:{utc_day(now)}"
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            inserted = conn.execute("""
                INSERT OR IGNORE INTO agent_events
                (source,source_id,kind,person_id,payload,created_at)
                VALUES ('youtube',?,'viewer_comment',?,?,?)
            """, (source_id, person_id,
                  json.dumps(payload, separators=(",", ":")), now))
            if inserted.rowcount != 1:
                conn.execute("COMMIT")
                return None
            conn.execute("""
                INSERT INTO agent_people(person_id,display_name,comment_count,memory_active,updated_at)
                VALUES (?,?,1,CASE WHEN 1>=? THEN 1 ELSE 0 END,?)
                ON CONFLICT(person_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    comment_count=agent_people.comment_count+1,
                    memory_active=CASE WHEN agent_people.comment_count+1>=? THEN 1
                                       ELSE agent_people.memory_active END,
                    updated_at=excluded.updated_at
            """, (person_id, clean_text(display_name, 80) or "viewer",
                  memory_threshold, now, memory_threshold))
            metadata = json.dumps({"source_id": source_id}, separators=(",", ":"))
            for thread_id in (day_thread, f"viewer:{person_id}"):
                conn.execute("""
                    INSERT INTO agent_turns
                    (thread_id,person_id,role,kind,text,metadata,created_at)
                    VALUES (?,?, 'user','viewer_comment',?,?,?)
                """, (thread_id, person_id, clean_text(text, 1000), metadata, now))
            row = conn.execute("SELECT * FROM agent_people WHERE person_id=?",
                               (person_id,)).fetchone()
            conn.execute("COMMIT")
            return dict(row)

    def person(self, person_id):
        if not person_id:
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM agent_people WHERE person_id=?",
                               (person_id,)).fetchone()
            return dict(row) if row else None

    def update_person_summary(self, person_id, summary):
        with self.connect() as conn:
            conn.execute("""
                UPDATE agent_people SET summary=?, summary_version=summary_version+1,
                    updated_at=? WHERE person_id=? AND memory_active=1
            """, (clean_text(summary, 1500), int(time.time()), person_id))

    def summary(self, thread_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM agent_summaries WHERE thread_id=?",
                               (thread_id,)).fetchone()
            return dict(row) if row else None

    def update_summary(self, thread_id, summary, through_turn_id):
        now = int(time.time())
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO agent_summaries(thread_id,summary,through_turn_id,version,updated_at)
                VALUES (?,?,?,1,?) ON CONFLICT(thread_id) DO UPDATE SET
                    summary=excluded.summary,
                    through_turn_id=excluded.through_turn_id,
                    version=agent_summaries.version+1,
                    updated_at=excluded.updated_at
            """, (thread_id, clean_text(summary, 3000), through_turn_id, now))

    def status(self):
        with self.connect() as conn:
            jobs = {row["status"]: row["count"] for row in conn.execute(
                "SELECT status,count(*) count FROM agent_jobs GROUP BY status")}
            people = conn.execute("SELECT count(*) total,sum(memory_active) active FROM agent_people").fetchone()
            return {"jobs": jobs, "people": {"total": people["total"],
                    "memory_active": people["active"] or 0}}


class MarketFeed:
    """Authoritative OHLC consumer with reconnect and atomic seed replacement."""

    def __init__(self, url, symbol, timeframes=("1m", "5m", "15m", "1h")):
        self.url = url
        self.symbol = symbol
        self.timeframes = tuple(timeframes)
        self._lock = threading.Lock()
        self._bars = {tf: {} for tf in self.timeframes}
        self._forming = {}
        self._connected = False
        self._last_received = 0.0
        self._stop = threading.Event()

    def start(self):
        if websocket is None or not self.url:
            return False
        threading.Thread(target=self._run, daemon=True, name="agent-market-feed").start()
        return True

    def stop(self):
        self._stop.set()

    def _apply(self, message):
        message_type = message.get("type")
        if message_type == "seed":
            key_tf = str(message.get("key", "")).partition("|")[2]
            bars = message.get("bars") or []
            if key_tf not in self._bars:
                return
            replacement = {bar.get("open_time"): bar for bar in bars
                           if bar.get("symbol") == self.symbol and bar.get("closed") is True}
            with self._lock:
                self._bars[key_tf] = replacement
                self._last_received = time.time()
            return
        bar = message.get("bar") or {}
        tf = bar.get("tf")
        if tf not in self._bars or bar.get("symbol") != self.symbol:
            return
        with self._lock:
            if message_type == "closed" and bar.get("closed") is True:
                self._bars[tf][bar.get("open_time")] = bar
                self._forming.pop(tf, None)
                if len(self._bars[tf]) > 1000:
                    del self._bars[tf][sorted(self._bars[tf])[0]]
            elif message_type == "forming" and bar.get("closed") is False:
                self._forming[tf] = bar
            self._last_received = time.time()

    def _run(self):
        retry = 1.0
        while not self._stop.is_set():
            connection = None
            try:
                connection = websocket.create_connection(self.url, timeout=15,
                                                         suppress_origin=True)
                for tf in self.timeframes:
                    connection.send(json.dumps({"action": "subscribe", "symbol": self.symbol,
                                                "tf": tf, "seed_bars": 500}))
                connection.settimeout(90)
                with self._lock:
                    self._connected = True
                retry = 1.0
                while not self._stop.is_set():
                    self._apply(json.loads(connection.recv()))
            except Exception as exc:
                with self._lock:
                    self._connected = False
                print(f"[agent-market] connection ended: {exc}", flush=True)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
            self._stop.wait(retry)
            retry = min(30.0, retry * 2)

    def snapshot(self):
        with self._lock:
            bars = {}
            for tf, values in self._bars.items():
                ordered = [values[key] for key in sorted(values)]
                bars[tf] = ordered[-2:]
            latest = self._forming.get("1m")
            if latest is None and bars.get("1m"):
                latest = bars["1m"][-1]
            price_as_of = 0.0
            if latest:
                price_as_of = parse_time(latest.get("updated_at") or
                                         latest.get("close_time") or
                                         latest.get("open_time"))
                if latest.get("closed") is True and not latest.get("close_time"):
                    price_as_of += 60
            return {
                "symbol": self.symbol,
                "connected": self._connected,
                "last_received": self._last_received,
                "price": float(latest["close"]) if latest else None,
                "price_as_of": price_as_of,
                "bars": bars,
            }


@dataclass(frozen=True)
class PeriodicSpec:
    name: str
    interval: int
    kind: str
    context_scope: str


class AgentRuntime:
    """Job registry, durable scheduler, scoped memory and graph executor."""

    def __init__(self, store, market, speaker, llm, signal_snapshot=None,
                 asset_label="Bitcoin", commentary_interval=120,
                 memory_threshold=10, enabled=True):
        self.store = store
        self.market = market
        self.speaker = speaker
        self.llm = llm
        self.signal_snapshot = signal_snapshot or (lambda _symbol, _tf: {})
        self.asset_label = clean_text(asset_label, 40) or market.symbol
        self.commentary_interval = max(60, int(commentary_interval))
        self.memory_threshold = max(2, int(memory_threshold))
        self.enabled = enabled
        self._stop = threading.Event()
        self._handlers = {
            "market_commentary": self._market_commentary,
            "person_compaction": self._person_compaction,
            "broadcast_compaction": self._broadcast_compaction,
            "subscription": self._subscription,
            "market_event": self._market_event,
        }
        self._periodic = (
            PeriodicSpec("market", self.commentary_interval,
                         "market_commentary", "topic"),
            PeriodicSpec("broadcast-compact", 3600,
                         "broadcast_compaction", "broadcast"),
        )
        self._graph = self._build_graph()

    def _build_graph(self):
        if StateGraph is None:
            return None
        graph = StateGraph(dict)
        graph.add_node("load_context", self._node_load_context)
        graph.add_node("execute", self._node_execute)
        graph.add_node("persist", self._node_persist)
        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "execute")
        graph.add_edge("execute", "persist")
        graph.add_edge("persist", END)
        return graph.compile()

    def start(self):
        self.market.start()
        if not self.enabled:
            return
        threading.Thread(target=self._scheduler, daemon=True,
                         name="agent-scheduler").start()
        threading.Thread(target=self._worker, daemon=True,
                         name="agent-worker").start()

    def stop(self):
        self._stop.set()
        self.market.stop()

    def _scheduler(self):
        while not self._stop.is_set():
            now = int(time.time())
            for spec in self._periodic:
                slot = now - now % spec.interval
                job_id = f"{spec.name}:{self.market.symbol}:{slot}"
                self.store.enqueue(job_id, spec.kind,
                                   {"slot": slot, "symbol": self.market.symbol},
                                   spec.context_scope, slot)
            self._stop.wait(10)

    def _worker(self):
        while not self._stop.is_set():
            job = self.store.claim()
            if job is None:
                self._stop.wait(1)
                continue
            state = {"job": job, "trace": []}
            try:
                if self._graph is not None:
                    state = self._graph.invoke(state)
                else:
                    state = self._node_load_context(state)
                    state = self._node_execute(state)
                    state = self._node_persist(state)
                result = state.get("result", {})
                self.store.finish(job["job_id"], result.get("status", "completed"), result)
            except Exception as exc:
                if job["attempts"] < 3:
                    self.store.finish(job["job_id"], "pending", error=exc,
                                      retry_at=int(time.time()) + 2 ** job["attempts"] * 10)
                else:
                    self.store.finish(job["job_id"], "failed", error=exc)

    def _node_load_context(self, state):
        job = state["job"]
        scope = job.get("context_scope", "none")
        context = {}
        if scope in ("broadcast", "topic", "person"):
            thread = f"broadcast:{utc_day()}"
            summary = self.store.summary(thread)
            context["broadcast_summary"] = summary["summary"] if summary else ""
            context["recent"] = self.store.recent_turns(thread, 20)
        if scope == "person":
            person = self.store.person(job["payload"].get("person_id"))
            if person and person["memory_active"]:
                context["person"] = person
        state["context"] = context
        state["trace"].append({"node": "load_context", "scope": scope})
        return state

    def _node_execute(self, state):
        handler = self._handlers.get(state["job"]["kind"])
        if handler is None:
            state["result"] = {"status": "skipped", "reason": "unknown_job"}
        else:
            state["result"] = handler(state["job"], state.get("context", {}))
        state["trace"].append({"node": "execute",
                               "status": state["result"].get("status")})
        return state

    def _node_persist(self, state):
        self.store.record_run(state["job"]["job_id"], state["trace"])
        return state

    def observe_viewer_message(self, message):
        source_id = clean_text(message.get("id"), 200)
        person_id = clean_text(message.get("channel_id") or message.get("author"), 200)
        if not source_id or not person_id:
            return False
        profile = self.store.observe_viewer_message(
            source_id, person_id, message.get("author"), message.get("text"),
            message, self.memory_threshold)
        if profile is None:
            return False
        count = profile["comment_count"]
        if count == self.memory_threshold or (count > self.memory_threshold and count % 5 == 0):
            self.store.enqueue(f"person-compact:{person_id}:{count}",
                               "person_compaction", {"person_id": person_id,
                               "comment_count": count}, "person")
        return True

    def reply_context(self, message):
        person_id = clean_text(message.get("channel_id") or message.get("author"), 200)
        profile = self.store.person(person_id)
        thread = f"broadcast:{utc_day()}"
        summary = self.store.summary(thread)
        context = {"recent_broadcast": self.store.recent_turns(thread, 12),
                   "broadcast_summary": summary["summary"] if summary else ""}
        if profile and profile["memory_active"]:
            context["viewer_memory"] = profile["summary"]
        return context

    def record_reply(self, message, reply, metadata=None):
        person_id = clean_text(message.get("channel_id") or message.get("author"), 200)
        thread = f"broadcast:{utc_day()}"
        self.store.add_turn(thread, "assistant", "spoken_reply", reply,
                            person_id, metadata)
        if person_id:
            self.store.add_turn(f"viewer:{person_id}", "assistant", "spoken_reply",
                                reply, person_id, metadata)

    def ingest_external(self, payload):
        kind = clean_text(payload.get("kind"), 40)
        source = clean_text(payload.get("source"), 80) or "external"
        source_id = clean_text(payload.get("id"), 200)
        if kind not in ("subscription", "market_event") or not source_id:
            raise ValueError("supported kind and stable id are required")
        if not self.store.record_event(source, source_id, kind, payload):
            return {"accepted": False, "duplicate": True}
        scope = "none" if kind == "subscription" else "topic"
        self.store.enqueue(f"external:{source}:{source_id}", kind, payload, scope)
        return {"accepted": True, "duplicate": False}

    def _market_commentary(self, job, context):
        slot = int(job["payload"].get("slot", 0))
        if time.time() - slot > self.commentary_interval:
            return {"status": "skipped", "reason": "expired_slot"}
        market = self.market.snapshot()
        source_age = time.time() - market.get("price_as_of", 0)
        if (not market["connected"] or market["price"] is None or
                time.time() - market["last_received"] > 90 or
                source_age < -30 or source_age > 120):
            return {"status": "skipped", "reason": "stale_market"}
        five = market["bars"].get("5m") or []
        if not five:
            return {"status": "skipped", "reason": "missing_closed_5m"}
        latest = five[-1]
        signals = self.signal_snapshot(self.market.symbol, "5m")
        price = market["price"]
        facts = {"asset": self.asset_label, "symbol": self.market.symbol,
                 "price": price, "price_text": f"{price:,.2f}",
                 "five_minute": latest, "signals": signals,
                 "as_of": int(time.time()), "source_age_seconds": round(source_age, 1)}
        recent = context.get("recent", [])[-8:]
        text, source = self.llm.market_commentary(facts, recent)
        if not verified_market_text(text, facts["price_text"]):
            direction = "higher" if float(latest["close"]) >= float(latest["open"]) else "lower"
            text = (f"{self.asset_label} is at {facts['price_text']}; "
                    f"the latest five-minute candle closed {direction}.")
            source = "verified-template"
        queued = self.speaker(text=text, source="agent-market", priority="low",
                              dedupe_key=job["job_id"],
                              cooldown_seconds=self.commentary_interval - 1,
                              metadata={"job_id": job["job_id"], "symbol": self.market.symbol,
                                        "price": price, "generator": source})
        if not queued.get("queued"):
            return {"status": "skipped", "reason": queued.get("reason", "speech_rejected")}
        self.store.add_turn(f"broadcast:{utc_day()}", "assistant",
                            "market_commentary", text, metadata={"facts": facts,
                            "job_id": job["job_id"]})
        return {"status": "completed", "text": text, "price": price,
                "generator": source}

    def _person_compaction(self, job, _context):
        person_id = job["payload"]["person_id"]
        profile = self.store.person(person_id)
        if not profile or not profile["memory_active"]:
            return {"status": "skipped", "reason": "memory_not_active"}
        turns = self.store.recent_turns(f"viewer:{person_id}", 40)
        summary, source = self.llm.compact_person(profile, turns)
        self.store.update_person_summary(person_id, summary)
        return {"status": "completed", "generator": source}

    def _broadcast_compaction(self, _job, _context):
        thread = f"broadcast:{utc_day()}"
        current = self.store.summary(thread)
        after = current["through_turn_id"] if current else 0
        turns = self.store.recent_turns(thread, 100, after)
        if not turns:
            return {"status": "skipped", "reason": "nothing_to_compact"}
        summary, source = self.llm.compact_broadcast(current, turns)
        self.store.update_summary(thread, summary, turns[-1]["id"])
        return {"status": "completed", "generator": source,
                "through_turn_id": turns[-1]["id"]}

    def _subscription(self, job, _context):
        name = clean_text(job["payload"].get("name"), 60) or "there"
        text = f"Welcome to the stream, {name}. Thanks for subscribing."
        queued = self.speaker(text=text, source="agent-subscription", priority="normal",
                              dedupe_key=job["job_id"], cooldown_seconds=86400,
                              metadata={"job_id": job["job_id"]})
        return {"status": "completed" if queued.get("queued") else "skipped",
                "text": text, "reason": queued.get("reason")}

    def _market_event(self, job, context):
        event = clean_text(job["payload"].get("text"), 400)
        if not event:
            return {"status": "skipped", "reason": "missing_event_text"}
        text, source = self.llm.external_event(event, context)
        queued = self.speaker(text=text, source="agent-event", priority="high",
                              dedupe_key=job["job_id"], cooldown_seconds=3600,
                              metadata={"job_id": job["job_id"], "generator": source})
        return {"status": "completed" if queued.get("queued") else "skipped",
                "text": text, "reason": queued.get("reason")}

    def status(self):
        result = self.store.status()
        market = self.market.snapshot()
        result.update({"enabled": self.enabled, "graph": "langgraph" if self._graph else "local",
                       "market": {"symbol": market["symbol"],
                                  "connected": market["connected"],
                                  "age_seconds": (round(time.time() - market["last_received"], 1)
                                                  if market["last_received"] else None),
                                  "price_age_seconds": (
                                      round(time.time() - market["price_as_of"], 1)
                                      if market.get("price_as_of") else None)},
                       "commentary_interval_seconds": self.commentary_interval,
                       "memory_threshold_comments": self.memory_threshold})
        return result
