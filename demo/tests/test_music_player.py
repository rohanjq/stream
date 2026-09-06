import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
