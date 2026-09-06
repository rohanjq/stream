import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["PERSIST_STATE"] = "false"

import music_player


class MusicPlayerTests(unittest.TestCase):
    def setUp(self):
        self.player = music_player.Player()

    def test_named_track_selection_is_case_insensitive(self):
        state = self.player.play("dream culture")
        self.assertEqual(state["track"]["id"], "dream_culture")

    def test_next_wraps_around_catalog(self):
        self.player._select(len(music_player.CATALOG) - 1)
        state = self.player.next()
        self.assertEqual(state["index"], 0)

    def test_private_download_fields_are_not_exposed(self):
        track = self.player.catalog()[0]
        self.assertNotIn("download_url", track)
        self.assertNotIn("file", track)
        self.assertNotIn("sha256", track)
        self.assertIn("credit", track)

    def test_volume_must_be_normalized(self):
        with self.assertRaises(ValueError):
            self.player.set_volume(1.1)

    def test_volume_change_does_not_signal_track_restart(self):
        self.player.set_volume(0.42)
        self.assertFalse(self.player.changed.is_set())

    def test_volume_change_updates_running_sink_in_place(self):
        process = mock.Mock()
        process.poll.return_value = None
        self.player.process = process
        with mock.patch.object(self.player, "_apply_volume", return_value=True) as apply:
            state = self.player.set_volume(0.42)
        apply.assert_called_once_with(process, 0.42, attempts=3, timeout=4)
        self.assertEqual(state["volume"], 0.42)
        self.assertFalse(self.player.changed.is_set())

    def test_sink_input_is_matched_by_ffmpeg_process_id(self):
        output = '''Sink Input #4
        Properties:
                application.process.id = "111"
Sink Input #9
        Properties:
                application.process.id = "222"
'''
        completed = mock.Mock(stdout=output)
        with mock.patch("music_player.subprocess.run", return_value=completed):
            self.assertEqual(self.player._sink_input_for_pid(222), 9)

    def test_volume_update_uses_one_timeout_budget(self):
        process = mock.Mock(pid=222)
        process.poll.return_value = None
        with mock.patch.object(self.player, "_sink_input_for_pid",
                       return_value=9) as sink_lookup, \
                mock.patch("music_player.subprocess.run") as run, \
                mock.patch("music_player.time.monotonic", side_effect=[10, 11, 12]):
            self.assertTrue(self.player._apply_volume(
                process, 0.42, attempts=3, timeout=4))
        self.assertEqual(sink_lookup.call_args.kwargs["timeout"], 3)
        self.assertEqual(run.call_args.kwargs["timeout"], 2)


if __name__ == "__main__":
    unittest.main()
