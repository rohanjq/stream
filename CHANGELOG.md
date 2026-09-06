# Change log

## 2026-09-06

### Operator console

- Added a dedicated responsive operations UI on port 8082 with Overview,
  Scene, Broadcast, Voice, YouTube, Audience, Music, Activity, and Settings
  views.
- Added a same-origin proxy for scene and compositor APIs, localhost-only
  server-side token injection, remote session-token support, endpoint status,
  and a browser-reachable scene preview.
- Added scene layout/timeframe controls, technical overlays, conversation-panel
  control, announce-first actions, broadcast callouts/polls/leaderboards, speech
  controls, YouTube templates/custom posts, audience policy editing, and music
  catalog/playback controls.
- Made `/api/health` authoritative for the global connection badge so the local
  panel-config endpoint cannot produce a false “Systems online” state.

### YouTube and mock chat

- Kept the official persistent `streamList` gRPC transport for inbound chat and
  added an external mock implementing that same boundary on port 18082.
- Added a mock browser UI and HTTP API on port 18083 for viewer messages,
  history, status, and channel-owned operator posts.
- Added `YOUTUBE_PUBLISH_URL` as an explicit local outbound adapter. Production
  leaves it empty and continues using the YouTube Data API with Google OAuth.
- Added owner metadata and exact outbound-message ID suppression so operator
  templates do not loop into AI replies or audience commands.
- Added `YOUTUBE_ALLOWED_CHANNEL_IDS` for production identity filtering and
  `YOUTUBE_ALLOWED_AUTHORS` for local mock convenience. Owner messages are now
  ignored by default.
- Fixed mock continuation handling when retained history passes 200 messages.
- Added recognition and tests for short timeframe commands such as
  `switch to 1m` while preserving announce-first execution.

### Music and reliability

- Added persistent music selection, playback state, and volume controls to the
  operator console.
- Changed volume updates to target the running FFmpeg PulseAudio sink input, so
  adjusting volume no longer restarts the track.
- Bounded all `pactl` work for one volume request to four seconds, below the
  scene API timeout.
- Added focused coverage for control-panel authorization/proxying, music state
  and in-place volume, mock publishing, identity filtering, and retention
  rollover.

### Operations

- Added port/configuration wiring for the operator service and updated deploy,
  status, test, startup, and container-image paths.
- Verified the full application suite, browser outage behavior, live mock
  inbound filtering, operator-to-mock publishing, self-message suppression,
  and uninterrupted volume updates.