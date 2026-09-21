import os
import sys
import time
import unittest
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_runtime


class FakeMarket:
    symbol = "BTCUSDT"

    def __init__(self, snapshot=None):
        self.value = snapshot or {}

    def start(self):
        return True

    def stop(self):
        pass

    def snapshot(self):
        return dict(self.value)


class FakeLLM:
    def market_commentary(self, facts, _recent):
        return f"Bitcoin is trading at {facts['price_text']} right now.", "test"

    def compact_person(self, _profile, turns):
        return f"remembered {len(turns)} turns", "test"

    def compact_broadcast(self, _current, turns):
        return f"compacted {len(turns)} turns", "test"

    def external_event(self, text, _context):
        return text, "test"


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = agent_runtime.AgentStore(
            os.path.join(self.temp.name, "agent.sqlite3"))
        self.spoken = []

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, market=None, threshold=10):
        def speaker(**kwargs):
            self.spoken.append(kwargs)
            return {"queued": True, "id": len(self.spoken)}

        return agent_runtime.AgentRuntime(
            self.store, market or FakeMarket(), speaker, FakeLLM(),
            memory_threshold=threshold, enabled=False)

    def test_duplicate_message_is_atomic_and_counted_once(self):
        runtime = self.runtime(threshold=3)
        message = {"id": "m1", "channel_id": "viewer-1",
                   "author": "Ada", "text": "hello"}
        self.assertTrue(runtime.observe_viewer_message(message))
        self.assertFalse(runtime.observe_viewer_message(message))
        self.assertEqual(self.store.person("viewer-1")["comment_count"], 1)
        self.assertEqual(len(self.store.recent_turns("viewer:viewer-1")), 1)

    def test_person_memory_activates_only_at_configured_threshold(self):
        runtime = self.runtime(threshold=3)
        for number in range(2):
            runtime.observe_viewer_message({
                "id": f"m{number}", "channel_id": "viewer-1",
                "author": "Ada", "text": f"comment {number}"})
        self.assertEqual(self.store.person("viewer-1")["memory_active"], 0)
        self.assertNotIn("viewer_memory", runtime.reply_context(
            {"channel_id": "viewer-1"}))

        runtime.observe_viewer_message({
            "id": "m3", "channel_id": "viewer-1",
            "author": "Ada", "text": "third comment"})
        self.assertEqual(self.store.person("viewer-1")["memory_active"], 1)
        claimed = self.store.claim()
        self.assertEqual(claimed["kind"], "person_compaction")

    def test_expired_job_lease_is_reclaimed(self):
        self.store.enqueue("job-1", "subscription", {"name": "Ada"})
        first = self.store.claim(lease_seconds=-1)
        second = self.store.claim()
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(second["attempts"], 2)

    def test_market_commentary_uses_fresh_authoritative_price_once(self):
        now = time.time()
        closed = {"symbol": "BTCUSDT", "tf": "5m",
                  "open_time": "2026-09-07T12:00:00Z", "open": 100.0,
                  "high": 102.0, "low": 99.0, "close": 101.0,
                  "closed": True}
        market = FakeMarket({"symbol": "BTCUSDT", "connected": True,
                             "last_received": now, "price_as_of": now,
                             "price": 12345.67,
                             "bars": {"1m": [], "5m": [closed]}})
        runtime = self.runtime(market)
        job = {"job_id": "market:BTCUSDT:1",
               "payload": {"slot": int(now)}, "context_scope": "topic"}
        result = runtime._market_commentary(job, {"recent": []})
        self.assertEqual(result["status"], "completed")
        self.assertIn("12,345.67", self.spoken[0]["text"])
        self.assertEqual(self.spoken[0]["priority"], "low")

    def test_market_commentary_rejects_fresh_connection_with_stale_bar(self):
        market = FakeMarket({"symbol": "BTCUSDT", "connected": True,
                             "last_received": time.time(), "price_as_of": 1,
                             "price": 12345.67, "bars": {"5m": []}})
        runtime = self.runtime(market)
        result = runtime._market_commentary(
            {"job_id": "market:stale",
             "payload": {"slot": int(time.time())}}, {})
        self.assertEqual(result, {"status": "skipped", "reason": "stale_market"})
        self.assertEqual(self.spoken, [])

    def test_subscription_job_has_no_conversation_context(self):
        runtime = self.runtime()
        accepted = runtime.ingest_external({
            "kind": "subscription", "source": "youtube", "id": "sub-1",
            "name": "Ada"})
        self.assertTrue(accepted["accepted"])
        job = self.store.claim()
        self.assertEqual(job["context_scope"], "none")
        state = runtime._node_load_context({"job": job, "trace": []})
        self.assertEqual(state["context"], {})
        duplicate = runtime.ingest_external({
            "kind": "subscription", "source": "youtube", "id": "sub-1",
            "name": "Ada"})
        self.assertTrue(duplicate["duplicate"])

    def test_market_claim_gate_rejects_extra_numbers(self):
        self.assertTrue(agent_runtime.verified_market_text(
            "Bitcoin is at 12,345.67 and holding steady.", "12,345.67"))
        self.assertFalse(agent_runtime.verified_market_text(
            "Bitcoin is at 12,345.67 with RSI at 70.", "12,345.67"))
        self.assertFalse(agent_runtime.verified_market_text(
            "Bitcoin is at 12,345.67 below its moving averages.", "12,345.67"))

    def test_rfc3339_market_timestamp_is_parsed(self):
        self.assertEqual(agent_runtime.parse_time("1970-01-01T00:01:00Z"), 60)


if __name__ == "__main__":
    unittest.main()
