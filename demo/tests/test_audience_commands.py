import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from audience_commands import AudienceCommandEngine, parse_builtin


def rule(**changes):
    base = {
        "enabled": True, "auto_execute": True,
        "roles": ["viewer", "superchat", "moderator"],
        "require_superchat": False, "min_superchat_micros": 0,
        "global_cooldown_seconds": 0, "user_cooldown_seconds": 0,
        "default_duration_seconds": 0, "max_duration_seconds": 600,
        "announce_result": True,
    }
    base.update(changes)
    return base


class AudienceCommandTests(unittest.TestCase):
    def test_requested_first_phrase_maps_to_chart_tool(self):
        proposal = parse_builtin("can you please change the chart to 1m")
        self.assertEqual(proposal["tool"], "chart.set_single")
        self.assertEqual(proposal["arguments"]["timeframe"], "1m")

    def test_short_switch_phrase_maps_to_chart_tool(self):
        proposal = parse_builtin("switch to 1m")
        self.assertEqual(proposal["tool"], "chart.set_single")
        self.assertEqual(proposal["arguments"]["timeframe"], "1m")

    def test_market_statement_is_not_a_command(self):
        self.assertIsNone(parse_builtin("I trade the 1m chart most days"))

    def test_temporary_duration_is_parsed_and_capped(self):
        calls = []
        engine = AudienceCommandEngine(
            {"chart.set_single": lambda args, _msg: calls.append(args) or {"ok": True}},
            {"chart.set_single": rule(max_duration_seconds=120)})
        decision = engine.process({
            "id": "m1", "author": "viewer", "channel_id": "c1",
            "text": "please switch the chart to 5m for 10 minutes",
        })
        self.assertTrue(decision["executed"])
        self.assertEqual(calls[0]["duration_seconds"], 120)

    def test_superchat_rule_blocks_regular_viewer(self):
        engine = AudienceCommandEngine(
            {"music.next": lambda _args, _msg: {"ok": True}},
            {"music.next": rule(require_superchat=True)})
        decision = engine.process({
            "id": "m2", "author": "viewer", "channel_id": "c2",
            "text": "please play the next song",
        })
        self.assertEqual(decision["reason"], "superchat_required")

    def test_named_song_maps_to_catalog_play_tool(self):
        proposal = parse_builtin("could you play Carefree please")
        self.assertEqual(proposal["tool"], "music.play")
        self.assertEqual(proposal["arguments"]["query"], "Carefree")

    def test_skip_song_maps_to_next_tool(self):
        proposal = parse_builtin("please skip this song")
        self.assertEqual(proposal["tool"], "music.next")

    def test_per_user_cooldown_denies_repeat(self):
        engine = AudienceCommandEngine(
            {"chart.set_single": lambda _args, _msg: {"ok": True}},
            {"chart.set_single": rule(user_cooldown_seconds=60)})
        message = {"id": "m3", "author": "viewer", "channel_id": "c3",
                   "text": "change the chart to 1m"}
        self.assertTrue(engine.process(message)["executed"])
        message["id"] = "m4"
        self.assertEqual(engine.process(message)["reason"], "user_cooldown")

    def test_deferred_command_does_not_execute_until_released(self):
        calls = []
        engine = AudienceCommandEngine(
            {"chart.set_single": lambda args, _msg: calls.append(args) or {"ok": True}},
            {"chart.set_single": rule()})
        message = {"id": "m5", "author": "viewer", "channel_id": "c5",
                   "text": "please change the chart to 1m"}
        decision = engine.process(message, defer_execute=True)
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["executed"])
        self.assertEqual(calls, [])
        completed = engine.execute_authorized(decision, message)
        self.assertTrue(completed["executed"])
        self.assertEqual(calls[0]["timeframe"], "1m")


if __name__ == "__main__":
    unittest.main()
