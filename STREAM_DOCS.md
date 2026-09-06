# Night Shift architecture and operations

This document describes the repository deployment, not a snapshot of one host.
The root `compose.yaml` is the source of truth.

## Components and ownership

| Component | Responsibility | Failure behavior |
|---|---|---|
| MediaMTX | Local RTMP handoff and raw HLS preview | Compose restarts it |
| App | Operator console, scene API, Chromium render, PulseAudio, music, speech, YouTube chat, raw encoder | Workers restart internally; health/restart is the final boundary |
| GStreamer compositor | Reads `/raw`, draws Cairo overlays, re-encodes video, passes AAC through | Watchdog exits on a stalled frame path; Compose restarts it |
| External OHLC service | Historical snapshot and forming-candle WebSocket events | Chart reconnects; URL is configured in `config/stream.env` |
| YouTube | gRPC live-chat input and RTMPS output | Chat backs off/reconnects; RTMP egress retries |
| External mock chat | Local gRPC chat input, browser UI, and HTTP channel publishing | Runs outside Compose; app reconnects when it returns |

All containers share MediaMTX's network namespace. The media path uses loopback
and RTMP is not published on the host. Scene, control, and preview ports bind to
localhost by default.

## End-to-end paths

Video:

```text
Chromium on Xvfb :99
  -> FFmpeg x11grab, H.264/AAC FLV
  -> rtmp://127.0.0.1:1935/raw
  -> FFmpeg RTMP bridge, MPEG-TS FIFO
  -> GStreamer tsdemux
  -> avdec_h264 -> cairooverlay -> x264enc
  -> AAC passthrough -> flvmux -> FIFO
  -> FFmpeg RTMPS egress -> YouTube
```

Audio:

```text
music FFmpeg (48 kHz stereo) ─┐
Kokoro/Piper speech (48 kHz) ─┼-> PulseAudio ytsink
browser audio (if any) ───────┘       -> ytsink.monitor
                                      -> one AAC encode in app FFmpeg
                                      -> GStreamer aacparse passthrough
```

The null sink runs at 48 kHz stereo with 8 x 10 ms fragments. Speech is
pre-rendered to PCM, normalized to -16 LUFS, resampled before PulseAudio, and
played serially. `PULSE_LATENCY_MSEC=200` absorbs short CPU spikes. If audio
crackles, inspect CPU saturation and PulseAudio underruns before changing
sample-rate math. Software WebGL is intentionally disabled because it
previously starved the audio mixer on CPU-only hosts.

Data and control:

```text
OHLC WebSocket -> browser chart feed -> in-place candle updates
YouTube streamList gRPC -> policy/router -> announce first -> action callback
operator console (:8082) -> scene API -> control state -> chart (no reload)
indicator/notifier -----> shared priority speech queue
```

The chart source and symbol come from `/api/runtime-config`, populated by
`OHLC_WS_URL` and `OHLC_SYMBOL`. No machine hostname is baked into HTML.

## Files and persistence

| Path | Purpose |
|---|---|
| `.env` | Private RTMP/OAuth/AI/control credentials (ignored) |
| `config/stream.env` | Tracked normal configuration |
| `compose.yaml` | Canonical complete stack |
| `demo/start.sh` | App-container supervisor and media encoder |
| `demo/control_panel_server.py` | Dedicated operator UI and scene API proxy |
| `demo/control/` | Primary responsive operator console |
| `demo/scene_server.py` | HTTP API, speech coordination, chat integration |
| `demo/youtube_chat.py` | Official YouTube gRPC consumer/outbound messages |
| `demo/audience_commands.py` | Extensible command policy/router |
| `demo/music_player.py` | Single-owner playlist and loopback API |
| `mock/mock_youtube_chat.py` | External gRPC/HTTP YouTube chat simulator |
| `demo/gstreamer/app.py` | Pipeline, bridges, and watchdog |
| `scripts/` | Doctor, deploy, status, logs, stop, and tests |

The `stream-state` volume stores `control.json`, `music.json`, and
`replies.log`. Deploy and stop operations retain it. Only an explicit
`compose down -v` erases it.

## Clean-machine deployment

1. Install Podman plus `podman-compose` (or Docker Compose), Git, curl, and
   OpenSSL.
2. Clone the repository.
3. Copy `.env.example` to `.env`, set mode 600, and supply secrets.
4. Set the reachable `OHLC_WS_URL` in `config/stream.env`.
5. Run `./scripts/doctor.sh`, `./scripts/test.sh`, then
   `./scripts/deploy.sh`.
6. Confirm both health endpoints and inspect the raw HLS preview.
7. Confirm YouTube Studio receives stable video and audio before going public.

The first build downloads Chromium, Piper, Kokoro, and the licensed playlist;
it is much slower than subsequent builds. Allow about 90 seconds after startup
for the full media chain to converge.

## Secret handling and rotation

Required production secrets are `YOUTUBE_RTMP_URL` and `CONTROL_TOKEN`.
YouTube chat additionally needs `YOUTUBE_CLIENT_ID`,
`YOUTUBE_CLIENT_SECRET`, and `YOUTUBE_REFRESH_TOKEN`. The access token is
optional and short lived. DeepSeek is optional.

Never put a stream key in `config/stream.env`, Compose YAML, shell history, or
API logs. Credentials pasted into chat or a ticket should be rotated: replace
the OAuth client secret, revoke/reissue the refresh token, and reset the
YouTube stream key before production.

## Operator console

The dedicated console at `http://127.0.0.1:8082/` is the primary operations
surface. It is served by `demo/control_panel_server.py`, which keeps the browser
on one origin and proxies scene requests under `/api/*` and compositor requests
under `/compositor-api/*`.

| View | Main operations |
|---|---|
| Overview | Program preview, authoritative service health, quick scenes, current music, recent conversation |
| Scene | Single/grid layouts, 1m/5m/15m/1h selection, optional announce-first changes, indicators, conversation panel |
| Broadcast | Program overlay, update callouts, polls, votes, and leaderboard |
| Voice | Immediate speech, queue state, and conversation-response testing |
| YouTube | Reply mode/cooldown, connection telemetry, template posts, and custom channel posts |
| Audience | Per-command enablement, roles, execution, paid-message, cooldown, duration, and announcement policy |
| Music | Current track, pause/resume, previous/next, volume, searchable licensed catalog |
| Activity | Viewer/host conversation and operational message history |
| Settings | Control token, runtime OHLC values, and endpoint-level status |

The console polls all control-plane services, but the global connection badge
uses `/api/health` as its authoritative signal. A successful `/panel-config`
response alone cannot report the stream as online. The embedded scene preview
uses a browser-reachable OHLC URL while the actual scene keeps its configured
container URL.

When bound only to localhost, `OPERATOR_TRUST_LOCAL=true` allows the proxy to
inject `CONTROL_TOKEN` server-side. The token is never embedded in downloaded
JavaScript. When the console is exposed beyond localhost, disable trusted-local
access and enter the token in Settings; it is retained only in that browser tab
session. Keep `STREAM_BIND_ADDRESS=127.0.0.1` unless a firewall or authenticated
reverse proxy protects the service.

## HTTP APIs

Operator console (`:8082`): the primary UI for scene layout, overlays, speech,
YouTube replies/posts, audience-command policy, music, activity, and runtime
status. It proxies `/api/*` to the loopback scene API so the browser uses one
origin and forwards the operator bearer token unchanged. `GET /healthz` checks
the console listener itself. `/compositor-api/*` proxies the GStreamer control
service for output layouts, update callouts, polls, votes, and leaderboards.

Scene service (`:8080`):

- `GET /api/health`, `/api/runtime-config`, `/api/control`
- `GET /api/youtube/status`, `/api/speech/status`, `/api/audience/status`
- `GET /api/music/status`, `/api/music/catalog`
- `POST /api/control` — layout/timeframes/overlays; silent unless announced
- `POST /api/speech/trigger` — queue operator/indicator speech
- `POST /api/music/{play,next,previous,pause,resume,volume}`
- `POST /api/youtube/publish` — non-character informational/promotional chat
- `POST /api/youtube/policy`, `/api/audience/policy`

Protected request example:

```bash
curl -H "Authorization: Bearer $CONTROL_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"timeframe":"15m","announce":true,"source":"admin"}' \
  http://127.0.0.1:8080/api/control
```

GStreamer service (`:7800`): `GET /health`, `GET /state`, and POST endpoints
`/layout`, `/log`, `/poll`, `/poll/vote`, `/leaderboard`. Writes use the same
bearer token. Without a token, writes are limited to loopback.

## Live chat and command policy

`youtube_chat.py` holds one TLS gRPC channel to the official
`liveChatMessages.streamList` method, forwards continuation tokens, and
reconnects with jittered exponential backoff. `RESOURCE_EXHAUSTED` receives a
minimum 60-second delay. Outbound messages are de-duplicated by ID.

Production inbound and outbound paths are deliberately separate:

```text
YouTube streamList gRPC -> normalize -> identity filter -> reply/command policy
operator publish queue -> YouTube Data API using dedicated Google OAuth account
```

Leave `YOUTUBE_PUBLISH_URL` empty in production. The configured OAuth identity
must have permission to post to the target live chat. The message ID returned
by YouTube is remembered for exact duplicate suppression. Owner suppression and
the production channel-ID allowlist provide the identity-level guard that keeps
operator templates and custom posts out of viewer processing.

Set `YOUTUBE_IGNORE_OWNER=true` to prevent channel-owner posts from entering
the reply and command pipeline. For restricted participation, set
`YOUTUBE_ALLOWED_CHANNEL_IDS` to a comma-separated list of immutable YouTube
channel IDs. `YOUTUBE_ALLOWED_AUTHORS` provides case-insensitive display-name
matching for local mocks only; display names are mutable and spoofable, so do
not use them as a production authorization boundary. When both allowlists are
empty, all non-ignored chat identities are processed.

Local mock mode changes only the endpoints, not the application pipeline:

```env
YOUTUBE_CHAT_TRANSPORT=grpc
YOUTUBE_GRPC_TARGET=host.containers.internal:18082
YOUTUBE_GRPC_INSECURE=true
YOUTUBE_LIVE_CHAT_ID=mock-live-chat
YOUTUBE_PUBLISH_URL=http://host.containers.internal:18083/api/channel-messages
YOUTUBE_IGNORE_OWNER=true
YOUTUBE_ALLOWED_AUTHORS=Mock Viewer
```

The mock marks operator posts as channel-owned. They remain visible in mock
history but owner suppression prevents AI review and command execution. A
different mock username is also visible but ignored while the author allowlist
above is active. See [mock/README.md](mock/README.md) for the full workflow.

The command layer is separate from transport. Each tool has policy for enabled
state, permissions, cooldown, paid-message requirements, and maximum frequency.
Cooldowns are zero for current testing. Before a public launch, configure
per-tool/global limits and decide which commands require Super Chat or moderator
status.

Actions with speech use an announce-first callback: the request is queued,
spoken, then applied. Chat, indicators, operator actions, and Super Chats share
one bounded priority speech queue, preventing simultaneous character responses.

## Audio verification

Inside the app container:

```bash
pactl list short sinks
pactl list short sink-inputs
curl -fsS http://127.0.0.1:8091/status
curl -fsS http://127.0.0.1:8080/api/speech/status
```

The expected sink is `ytsink`; music owns one FFmpeg sink input, with a second
temporary input only while speech plays. The browser's synthesized-audio copy
is for lipsync and remains muted to avoid echo.

Music selection, playback state, and volume persist in `stream-state`. Volume
updates locate the active FFmpeg sink input by process ID and call `pactl`
without setting the player's restart event, so the current track continues from
the same position. Lookup and update attempts share one four-second deadline,
which remains below the scene API's five-second music request timeout.

## Recovery and maintenance

- Each FFmpeg connection retries after three seconds.
- Music, scene server, Xvfb, and Chromium are supervised in the app container.
- GStreamer exits after `STALL_TIMEOUT_S` without frames; Compose restarts it,
  providing a clean FIFO recovery boundary.
- Control and music settings survive recreation via the named volume.
- `make deploy` rebuilds/recreates without removing that volume.
- The external mock retains 200 messages. Its continuation cursor remains
  monotonic when old entries are trimmed, so connected readers do not freeze at
  the retention boundary.

For an upgrade: run tests, deploy, watch logs, verify health, verify HLS, and
then verify YouTube Studio. Keep the broadcast private until audio/video remain
stable for several minutes.

## Known constraints

- OHLC is external and must implement the chart WebSocket contract. Production
  monitoring should add candle-age alerts, not just connection checks.
- FIFO handoff can stall after a writer restart; the watchdog contains this.
- 720p30 software rendering and two H.264 encodes need CPU headroom.
- The bundled Zscaler certificate is development-network-specific.
- This repository has no declared software license; choose one before public
  redistribution.

## Validation

Run the tracked application suite and the mock-specific rollover test:

```bash
./scripts/test.sh
uv run --with grpcio==1.74.0 --with grpcio-tools==1.74.0 \
  python -m unittest mock/test_mock_youtube_chat.py
```

The application suite covers console proxy authorization, music controls,
YouTube normalization/publishing/identity policy, command routing, and scene
behavior. The mock test covers continuation after the 200-message retention
window rolls over.
