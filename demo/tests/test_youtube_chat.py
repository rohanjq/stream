import os
import io
import json
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import ai
from youtube_chat import OAuthTokens, YouTubeBridge, YouTubeClient


class FakeTokens:
    configured = True


class FakeClient:
    def __init__(self):
        self.tokens = FakeTokens()
        self.calls = 0
        self.sent = []

    def active_live_chat_id(self):
        return "live-chat"

    def messages(self, _chat_id, _page_token=None):
        self.calls += 1
        old = {
            "id": "old", "snippet": {"type": "textMessageEvent",
            "displayMessage": "old message"},
            "authorDetails": {"displayName": "Old Viewer"},
        }
        new = {
            "id": "new", "snippet": {"type": "textMessageEvent",
            "displayMessage": "new message"},
            "authorDetails": {"displayName": "New Viewer"},
        }
        return {"items": [old] if self.calls == 1 else [old, new],
                "nextPageToken": f"p{self.calls}", "pollingIntervalMillis": 1}

    def send(self, _chat_id, text):
        self.sent.append(text)
        return {"id": "sent"}


class YouTubeBridgeTests(unittest.TestCase):
    def test_token_json_millisecond_expiry_is_supported(self):
        tokens = OAuthTokens("id", "secret", "refresh", "access", 2_000_000_000_000)
        self.assertEqual(tokens.expires_at, 2_000_000_000)

    def test_first_poll_is_backlog_and_only_new_message_is_emitted(self):
        received = []
        bridge = None

        def callback(message):
            received.append(message)
            bridge._stop.set()

        bridge = YouTubeBridge(FakeClient(), callback, transport="rest")
        bridge.start()
        deadline = time.time() + 3
        while not received and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual([message["id"] for message in received], ["new"])

    def test_owner_is_ignored_and_superchat_metadata_is_preserved(self):
        bridge = YouTubeBridge(FakeClient(), lambda _message: None)
        owner = {"id": "1", "snippet": {"type": "textMessageEvent",
                 "displayMessage": "channel post"},
                 "authorDetails": {"displayName": "Owner", "isChatOwner": True}}
        self.assertIsNone(bridge._normalize(owner))
        paid = {"id": "2", "snippet": {"type": "superChatEvent",
                "superChatDetails": {"userComment": "please show 5m",
                "amountDisplayString": "$5.00", "tier": 2}},
                "authorDetails": {"displayName": "Supporter"}}
        message = bridge._normalize(paid)
        self.assertEqual(message["text"], "please show 5m")
        self.assertEqual(message["purchase_amount"], "$5.00")

    def test_identity_allowlist_filters_before_message_processing(self):
        bridge = YouTubeBridge(
            FakeClient(), lambda _message: None,
            allowed_channel_ids=["trusted-channel"],
            allowed_authors=["Local Tester"])
        base = {"snippet": {"type": "textMessageEvent",
                            "displayMessage": "next song"}}
        rejected = {**base, "id": "1", "authorDetails": {
            "displayName": "Untrusted", "channelId": "other-channel"}}
        allowed_by_id = {**base, "id": "2", "authorDetails": {
            "displayName": "Renamed", "channelId": "trusted-channel"}}
        allowed_by_name = {**base, "id": "3", "authorDetails": {
            "displayName": "local tester", "channelId": "local-mock"}}

        self.assertIsNone(bridge._normalize(rejected))
        self.assertEqual(bridge._normalize(allowed_by_id)["id"], "2")
        self.assertEqual(bridge._normalize(allowed_by_name)["id"], "3")

    def test_grpc_message_is_normalized_for_existing_command_pipeline(self):
        bridge = YouTubeBridge(FakeClient(), lambda _message: None,
                               transport="grpc", ignore_owner=False,
                               allowed_channel_ids=["channel"])
        item = SimpleNamespace(
            id="g1",
            snippet=SimpleNamespace(
                type=1, published_at="2026-09-06T00:00:00Z",
                display_message="play Carefree",
                text_message_details=SimpleNamespace(message_text="play Carefree"),
                super_chat_details=SimpleNamespace(
                    user_comment="", amount_display_string="",
                    amount_micros=0, tier=0)),
            author_details=SimpleNamespace(
                display_name="Viewer", channel_id="channel",
                is_chat_owner=False, is_chat_moderator=False,
                is_chat_sponsor=False))
        message = bridge._normalize_grpc(item)
        self.assertEqual(message["id"], "g1")
        self.assertEqual(message["text"], "play Carefree")
        item.author_details.channel_id = "untrusted-channel"
        self.assertIsNone(bridge._normalize_grpc(item))

    def test_outbound_is_explicit_and_length_limited(self):
        bridge = YouTubeBridge(FakeClient(), lambda _message: None,
                               live_chat_id="live-chat")
        item = bridge.publish("A help message", category="help")
        self.assertEqual(item["category"], "help")
        with self.assertRaises(ValueError):
            bridge.publish("x" * 201)

    def test_mock_publisher_posts_text_and_returns_message(self):
        response = io.BytesIO(json.dumps({
            "accepted": True, "message": {"id": "mock-channel-1"},
        }).encode())
        client = YouTubeClient(OAuthTokens("", "", ""),
                               publish_url="http://mock/api/channel-messages")
        with mock.patch("youtube_chat.urllib.request.urlopen",
                        return_value=response) as urlopen:
            message = client.send("ignored-in-mock-mode", "Channel update")
        request = urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"text": "Channel update"})
        self.assertEqual(message["id"], "mock-channel-1")

class ReplySelectionTests(unittest.TestCase):
    def test_offline_selector_skips_chatter_and_answers_questions(self):
        with mock.patch.object(ai, "AI_BASE", ""), mock.patch.object(ai, "AI_KEY", ""):
            speak, reply, source = ai.choose_live_reply("nice chart", "Viewer")
            self.assertFalse(speak)
            speak, reply, source = ai.choose_live_reply("what timeframe is this?", "Viewer")
            self.assertTrue(speak)
            self.assertTrue(reply)


if __name__ == "__main__":
    unittest.main()
