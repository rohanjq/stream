#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
python3 -m unittest discover -s demo/tests -p 'test_*.py'
python3 -m py_compile \
  demo/ai.py demo/audience_commands.py demo/music_fetch.py \
  demo/music_player.py demo/scene_server.py demo/control_panel_server.py \
  demo/youtube_chat.py \
  demo/gstreamer/app.py demo/gstreamer/control.py demo/gstreamer/draw.py \
  demo/gstreamer/state.py
