import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from mock_youtube_chat import ChatState


class ChatStateTests(unittest.TestCase):
    def test_cursor_continues_after_retention_rollover(self):
        state = ChatState()
        for index in range(200):
            state.add("Viewer", f"message {index}")

        _messages, cursor = state.snapshot_with_offset()
        state.add("Viewer", "message 200")
        messages, next_cursor = state.wait_after(cursor, timeout=0)

        self.assertEqual([message["text"] for message in messages], ["message 200"])
        self.assertEqual(next_cursor, 201)
        self.assertEqual(len(state.snapshot()), 200)


if __name__ == "__main__":
    unittest.main()