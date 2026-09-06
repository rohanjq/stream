# Night Shift architecture and operations

This document describes the repository deployment, not a snapshot of one host.
The root `compose.yaml` is the source of truth.

## Components and ownership

| Component | Responsibility | Failure behavior |
|---|---|---|
| MediaMTX | Local RTMP handoff and raw HLS preview | Compose restarts it |
| App | Scene API, Chromium render, PulseAudio, music, speech, YouTube chat, raw encoder | Workers restart internally; health/restart is the final boundary |
| GStreamer compositor | Reads `/raw`, draws Cairo overlays, re-encodes video, passes AAC through | Watchdog exits on a stalled frame path; Compose restarts it |
| External OHLC service | Historical snapshot and forming-candle WebSocket events | Chart reconnects; URL is configured in `config/stream.env` |
| YouTube | gRPC live-chat input and RTMPS output | Chat backs off/reconnects; RTMP egress retries |

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
operator HTTP API ------> control state -> postMessage -> chart (no reload)
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
| `demo/scene_server.py` | HTTP API, speech coordination, chat integration |
| `demo/youtube_chat.py` | Official YouTube gRPC consumer/outbound messages |
| `demo/audience_commands.py` | Extensible command policy/router |
| `demo/music_player.py` | Single-owner playlist and loopback API |
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

## HTTP APIs

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

## Recovery and maintenance

- Each FFmpeg connection retries after three seconds.
- Music, scene server, Xvfb, and Chromium are supervised in the app container.
- GStreamer exits after `STALL_TIMEOUT_S` without frames; Compose restarts it,
  providing a clean FIFO recovery boundary.
- Control and music settings survive recreation via the named volume.
- `make deploy` rebuilds/recreates without removing that volume.

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
