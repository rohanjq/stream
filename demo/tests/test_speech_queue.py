import os
import queue
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["PERSIST_STATE"] = "false"

import scene_server


class SpeechQueueTests(unittest.TestCase):
    def setUp(self):
        # The module worker remains blocked on the original queue. A fresh
        # queue lets these tests inspect scheduling without synthesizing audio.
        scene_server._speech_queue = queue.PriorityQueue(maxsize=8)
        scene_server._speech_dedupe_until = {}

    def test_priority_then_fifo_order(self):
        first = scene_server.queue_speech("normal one", priority="normal")
        urgent = scene_server.queue_speech("indicator", priority="high")
        second = scene_server.queue_speech("normal two", priority="normal")
        jobs = [scene_server._speech_queue.get_nowait()[2] for _ in range(3)]
        self.assertEqual([job["text"] for job in jobs],
                         ["indicator", "normal one", "normal two"])
        self.assertTrue(first["queued"] and urgent["queued"] and second["queued"])

    def test_dedupe_key_gets_default_cooldown(self):
        first = scene_server.queue_speech("price at order block", source="indicator",
                                          dedupe_key="BTC:15m:OB:80000")
        duplicate = scene_server.queue_speech("price at order block", source="indicator",
                                              dedupe_key="BTC:15m:OB:80000")
        self.assertTrue(first["queued"])
        self.assertEqual(duplicate,
                         {"queued": False, "reason": "duplicate_or_cooldown"})

    def test_invalid_priority_is_rejected(self):
        with self.assertRaises(ValueError):
            scene_server.queue_speech("hello", priority="immediate")


if __name__ == "__main__":
    unittest.main()
