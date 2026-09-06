# Night Shift live stream

A headless, continuously running YouTube show: live multi-timeframe charts,
licensed music, queued AI speech, YouTube live-chat commands, and a GStreamer
overlay compositor. It runs without OBS and changes layouts without reloading
the stream.

## Deploy from a clean clone

Requirements: Linux, Podman 4+ with `podman-compose` (recommended) or Docker
with Compose, at least 6 CPU cores and 8 GB RAM, and a reachable OHLC WebSocket.
A GPU is optional; the 3D character is disabled by default on CPU-only hosts.

```bash
git clone https://github.com/rohanjq/stream.git
cd stream
cp .env.example .env
chmod 600 .env
# Edit .env: YouTube RTMP URL, OAuth values, control token, optional AI key.
# Edit config/stream.env only for non-secret deployment settings.

./scripts/doctor.sh
./scripts/deploy.sh
./scripts/status.sh
```

The deployment script builds both local images, starts the stack, and waits up
to two minutes for the scene health endpoint. It does not delete persistent
state during redeploys.

Local endpoints (bound to `127.0.0.1` by default):

- Primary operator console: `http://127.0.0.1:8082/`
- Broadcast scene: `http://127.0.0.1:8080/`
- Raw pre-compositor HLS preview: `http://127.0.0.1:8888/raw/`
- Scene health: `http://127.0.0.1:8080/api/health`
- GStreamer health: `http://127.0.0.1:7800/health`

The operator console is the primary day-to-day UI. Its views cover service
health, scene layouts and timeframes, chart overlays, broadcast graphics,
speech, YouTube reply policy and channel posts, audience-command permissions,
music, activity history, and runtime settings. See
[STREAM_DOCS.md](STREAM_DOCS.md#operator-console) for the complete control and
authentication model.

Set `STREAM_BIND_ADDRESS=0.0.0.0` in `.env` only behind a firewall/reverse
proxy. Change `OPERATOR_HOST_PORT` if port 8082 is occupied. Write endpoints
use `CONTROL_TOKEN`. The localhost-only console applies that token server-side;
when bound beyond localhost, enter it in Settings and it remains scoped to that
browser tab session.

## Day-to-day commands

```bash
make status
make logs
make test
make deploy       # rebuild and recreate services
make stop         # keeps the durable state volume
```

Use `./scripts/logs.sh app` or `./scripts/logs.sh compositor` to follow one
service. Do not run `compose down -v` unless you intend to erase saved chart,
music, and volume state.

## Configuration model

- `.env` — secrets and machine-private values; ignored by Git.
- `.env.example` — safe secret template.
- `config/stream.env` — normal versioned runtime settings.
- `compose.yaml` — the canonical production topology.
- `stream-state` volume — durable control state, music state, and reply log.

The older `demo/ai.env` and `demo/youtube.env` files remain ignored for the
currently running legacy deployment, but new machines should use root `.env`.

## Local YouTube mock

The external mock provides the same gRPC inbound boundary used in production,
plus an HTTP chat UI and an explicit channel-publish endpoint. It supports
viewer messages, operator templates/custom posts, owner semantics, and bounded
history without requiring Google OAuth. See [mock/README.md](mock/README.md) for
setup, endpoints, identity filtering, and tests.

Production must leave `YOUTUBE_PUBLISH_URL` empty. Outbound channel messages
then use the dedicated Google account configured by the OAuth variables in
`.env`. Restrict inbound processing with immutable
`YOUTUBE_ALLOWED_CHANNEL_IDS`; display-name matching is intended only for local
mock use.

## Architecture

```text
OHLC WebSocket ───────────────┐
YouTube gRPC live chat ───────┼─> scene server / command router / speech queue
licensed local music ─────────┘                 │
                                                v
Xvfb + Chromium + PulseAudio ──FFmpeg──> MediaMTX /raw
                                                │
                                                v
                                  GStreamer cairo compositor
                                                │
                                                v
                                      FFmpeg ──> YouTube RTMPS
```

GStreamer owns visual compositing and final video encoding. AAC audio is mixed
once in PulseAudio, encoded by the app, and passed through by the compositor.
FFmpeg is deliberately used for RTMP ingress/egress because it is more reliable
with MediaMTX and YouTube than GStreamer's RTMP plugins.

See [STREAM_DOCS.md](STREAM_DOCS.md) for operations, APIs, failure recovery,
audio details, secret rotation, and the full component map.
See [CHANGELOG.md](CHANGELOG.md) for the dated summary of delivered changes.

## Important operational notes

- The YouTube chat client uses the official persistent gRPC stream with
  continuation tokens and exponential reconnect backoff. It does not poll once
  per second.
- Local mock mode can route operator channel posts to the external mock with
  `YOUTUBE_PUBLISH_URL`; production leaves it empty and uses YouTube OAuth.
- Owner posts are ignored by default. Optional channel-ID and local author
  allowlists are applied before AI replies or audience commands.
- Music volume changes update the running PulseAudio sink input in place, so
  they do not restart the track; `pactl` work is bounded below the API timeout.
- Speech from chat, indicators, operator actions, and Super Chats shares one
  priority queue. Only one utterance plays at a time, with a two-second gap.
- Audience command cooldowns are currently zero in
  `demo/audience_commands.json`; production policy should be tightened before
  opening the stream broadly.
- Music files are downloaded into the image from the tracked CC BY catalog;
  attribution is displayed in the scene and retained in the catalog.
- OAuth credentials and stream keys previously shared in chat should be
  rotated before a public deployment.

## Corporate TLS interception

The image contexts currently contain a Zscaler root certificate because this
development host uses TLS interception for dependency downloads. Replace those
certificates with your organization's CA when required. On a normal network,
remove the two `COPY zscaler-root-ca.pem` lines and the certificate files.
