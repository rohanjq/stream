# YouTube live-stream stack — headless AI trading-desk stream (GStreamer)

This repo is a working, verified prototype of a headless (no monitor) live
stream: a charting web app + a 3D AI host character (reads chat, speaks via
TTS, lip-syncs), captured and re-composited with dynamic overlay layouts
(event log / poll / leaderboard) that can be switched **live, via a plain
HTTP API, with no restart** — and pushed out as an RTMP stream.

**The stack is two cooperating pieces:**
1. `demo/` — a headless browser (Chromium) renders the actual scene (chart
   + 3D character + chat) and pushes it out as RTMP. This is the base
   video/audio source. It's not "the demo" on its own — it's what the
   compositor below composites on top of.
2. `demo/gstreamer/` — a Python + GStreamer process that pulls that base
   scene, draws the switchable overlay panels via Cairo, re-encodes, and
   republishes. **This is the actual point of this repo** — run both
   together (see "Running it" below).

**If you're an agent picking this up cold:** read this whole file before
running anything. Everything below was verified working in a real session
on a GPU-less Podman VM (Apple Silicon, 5 vCPU, 2GB RAM) — commands are
copy-pasted from what actually ran, not guessed. Where something is a real
open problem rather than a solved one, it's marked as such — don't assume
it's fixed just because it's documented.

## What's here

```
demo/                  Base scene: Chromium renders chart + 3D character +
                        chat, ffmpeg (x11grab) captures and pushes RTMP.
                        Required dependency of demo/gstreamer/ — always run
                        both together.
demo/gstreamer/        THE compositor. Pulls the base scene's RTMP, draws
                        dynamic overlay panels (log/poll/leaderboard) live
                        via Cairo, re-encodes, republishes. Controlled by
                        a tiny HTTP API — this is what makes layouts
                        switchable without restarting anything, the whole
                        point of this repo. Has one known unresolved edge
                        case — see "Known constraints" below.
demo/charts/            The actual charting web app (candlesticks, EMAs,
                        market-structure overlays, replay mode). This is
                        someone's existing app, copied in read-only aside
                        from one redaction noted below — do not otherwise
                        modify these files; treat as vendored.
demo/scene/             The composited "stream scene" page: chart iframe +
                        3D VRM character (Ask Me Anything panel) + ticker +
                        countdown + chat panel. This is what demo/'s
                        Chromium renders and captures.
REMOTE_AGENT_PLAN.md   A separate, more thorough plan for a REAL
                        production deployment on an AlmaLinux GPU box
                        streaming to actual YouTube — read this if the
                        goal is production, not a local test stream.
```

(A third compositor built on Smelter, github.com/software-mansion/smelter,
was also evaluated — deliberately **not** included here: it's ~25x
heavier on CPU for the same job as GStreamer, and its license caps
unrestricted real-time/live use. GStreamer is the one to use.)

## Prerequisites

- Podman (or Docker — commands below use `podman`, swap the binary name if
  you're on Docker; everything else is identical).
- `ffplay`/`ffmpeg` on the host is handy for viewing/debugging but not
  required — you can watch via a browser (HLS) instead.
- Enough CPU: this whole stack is **software-rendered** (no GPU
  passthrough assumed). Expect the base-scene container alone to use
  150-400% CPU (1.5-4 cores) depending on settings — see "Known
  constraints" below. If you have a real GPU available, this stack
  currently doesn't use it (see REMOTE_AGENT_PLAN.md for the GPU/NVENC
  production path).

## Secrets — set these up before building

**Never commit real values for these.** `demo/ai.env` is gitignored;
`demo/ai.env.example` is the template.

```bash
cp demo/ai.env.example demo/ai.env
# then edit demo/ai.env and fill in:
#   DEEPSEEK_URL   — your OpenAI-compatible chat endpoint
#   DEEPSEEK_KEY    — its API key
#   DEEPSEEK_MODEL  — model/deployment name
```

If you don't have a real key yet, leave `DEEPSEEK_KEY` blank — the app
falls back to a canned response so the pipeline still runs and streams;
you just won't get real AI replies (see `demo/ai.py`).

If the endpoint is **IP-allowlisted** (common for Azure AI Foundry
deployments), you must add whichever machine actually runs the container
to that allowlist, or every chat call will 403 and silently fall back to
the canned responder. `curl` the endpoint directly first to confirm before
assuming the app is broken:
```bash
curl -s https://YOUR_ENDPOINT/chat/completions \
  -H "Authorization: Bearer YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"model":"YOUR_MODEL","messages":[{"role":"user","content":"hi"}]}'
```

The charting app (`demo/charts/`) also has an AllTick data-fetch token,
redacted to `process.env.ALLTICK_TOKEN`. **You don't need this for a test
stream** — the default mode is `mode=replay`, which plays back bundled
static data (`data_gold.js` etc.) with zero network calls. Only set
`ALLTICK_TOKEN` if you specifically want live-market mode.

## Corporate proxy / TLS interception

If your build network has a corporate TLS-inspecting proxy (Zscaler or
similar), container builds that fetch anything over HTTPS (Piper TTS
binaries, three.js, the VRM model, GStreamer's own runtime downloads) will
fail with vague SSL/OpenSSL errors — **not** a network-down error, a
certificate-trust error, because the container's fresh trust store doesn't
have your proxy's root CA even though your host OS does.

Fix: export your proxy's root CA and bake it into the image before any
`RUN curl`/`wget` step:
```bash
# macOS example — adjust the -c filter to match your proxy's CA name
security find-certificate -a -c "Zscaler Root CA" -p \
  /Library/Keychains/System.keychain > demo/gstreamer/zscaler-root-ca.pem
```
Both Containerfiles (`demo/`, `demo/gstreamer/`) already do this
(`COPY zscaler-root-ca.pem ...` + `update-ca-certificates`) — verified
necessary on the network this was built on (Piper's download otherwise
fails with `SSL certificate problem: unable to get local issuer
certificate`). If your network doesn't need it, leave the file as-is
(harmless no-op) or delete the `COPY`/`update-ca-certificates` lines.

## Running it

Both pieces, in one pod, in this order — base scene must be reachable by
the compositor:

```bash
cd demo
podman build -t ytstream-demo -f Containerfile .
podman build -t ytgst -f gstreamer/Containerfile gstreamer

podman pod create --name ytdemo -p 8080:8080 -p 7800:7800 -p 8888:8888 -p 8889:8889

podman run -d --pod ytdemo --name ytdemo-mtx --restart=always \
  docker.io/bluenviron/mediamtx:latest

podman run -d --pod ytdemo --name ytdemo-gst --restart=always \
  -e FPS=30 \
  ytgst

sleep 3   # let the compositor's RTMP listener come up before the scene tries to connect

podman run -d --pod ytdemo --name ytdemo-app --restart=always \
  --shm-size=512m --env-file ai.env --cpus=3.5 \
  -e RTMP_TARGET=rtmp://127.0.0.1:1935/raw -e FPS=30 \
  ytstream-demo
```

Give it ~40-60s to fully converge (Xvfb → Chromium → base ffmpeg →
compositor's ingest bridge → compositor's egress, each with independent
3s retry loops — multiple pieces have to come up before the first frame
flows end to end; that's normal, not a hang). Then:

- **Watch it:** open `http://localhost:8888/live/` in a browser (HLS,
  ~5-10s latency), or `ffplay rtmp://localhost:1935/live` for near-zero
  latency.
- **Type a question for the AI host:** `http://localhost:8080/ask.html`
  (this hits the base scene directly, port 8080)
- **Raw base scene (debug, pre-compositing):** `http://localhost:8080/`

To confirm it's actually healthy rather than just "not crashed":
```bash
podman logs ytdemo-mtx 2>&1 | grep "path live.*online"   # should appear
podman stats --no-stream                                  # base app: 150-400% CPU is normal, gst: 1-40% is normal
```

### Switching layouts live (the actual point of this repo)

The compositor exposes a control API (default port 7800) — switching
layouts is a plain HTTP call, no restart, no OBS:
```bash
curl -s http://localhost:7800/layout -H "Content-Type: application/json" -d '{"layout":"logs"}'
curl -s http://localhost:7800/log    -H "Content-Type: application/json" -d '{"text":"some event"}'
curl -s http://localhost:7800/poll   -H "Content-Type: application/json" -d '{"question":"...","options":["A","B"]}'
curl -s http://localhost:7800/poll/vote -H "Content-Type: application/json" -d '{"option":"A"}'
curl -s http://localhost:7800/leaderboard -H "Content-Type: application/json" -d '{"entries":[{"name":"x","score":1}]}'
curl -s http://localhost:7800/state  # current state, GET
```
Valid `layout` values: `main`, `logs`, `poll`, `leaderboard`. `main` shows
just the base scene with no overlay panel.

### Multi-timeframe chart

The charting app supports showing several timeframes at once in a grid —
already wired up in `demo/scene/index.html`'s chart iframe:
```
/charts/chart.html?mode=replay&tf=1m,5m,15m,1h&stream=1
```
Change the `tf=` list (valid keys: `1m,5m,15m,30m,1h`) to add/remove panes;
`chart.js` handles the grid layout automatically for however many you list.

### Tuning knobs

Base scene (`demo/` container), env vars:

| Var | Default | Notes |
|---|---|---|
| `WIDTH`/`HEIGHT` | 1280/720 | Resolution — was previously lowered to 960x540 as a bandaid; **don't do that**, it didn't address the actual bottleneck (see below) and just looks worse. |
| `FPS` | 30 | Requires `-thread_queue_size 1024` on x11grab (already set in `start.sh`) — without it, ffmpeg silently caps at ~7fps regardless of this setting, no error, just wrong. If you ever rewrite `start.sh`'s ffmpeg command, keep that flag. |
| `VBITRATE` | 2500k | Bump if quality looks soft; CPU cost of a higher bitrate is negligible compared to resolution/fps. |

Compositor (`demo/gstreamer/` container), env vars:

| Var | Default | Notes |
|---|---|---|
| `FPS` | 24 | Match this to the base scene's `FPS` — used to compute `key-int-max` (2s GOP). |
| `VBITRATE_KBPS` | 4000 | Final output bitrate. Matches YouTube's published spec for 720p30 (~4 Mbps) — see REMOTE_AGENT_PLAN.md for the full encoder-spec table if targeting real YouTube. |
| `ENC_THREADS`/`DEC_THREADS` | 2/2 | Explicit rather than "0=auto" — auto-detection reads the *host's* core count, not this container's `--cpus` quota, and can over-subscribe on a capped/shared VM. |
| `STALL_TIMEOUT_S` | 20 | Watchdog: exit if no frame drawn in this long (see Known constraints). |
| `STARTUP_GRACE_S` | 60 | Grace period before the watchdog is armed, so normal multi-process startup convergence doesn't trip it. |

## Known constraints — read before assuming something's broken

**The 3D character (WebGL, software-rendered) is the dominant CPU cost,**
not the chart, not the compositor. On this dev VM it alone used
200-300%+ CPU via Chromium's SwiftShader software GL. `demo/scene/char3d.js`
throttles its actual `renderer.render()` calls to 15fps internally (state
updates like blink/lipsync still run every frame) specifically to reduce
this — if you need more headroom, that's the first thing to reduce further
(lower internal render fps, simplify the model, or run somewhere with a
real GPU). The compositor itself (`demo/gstreamer/`) is comparatively
tiny — measured at 1-40% CPU doing full 720p decode+overlay+encode — so
don't spend time optimizing it before addressing the character.

**`-thread_queue_size` matters more than it looks.** Without it on the
x11grab input, ffmpeg drops to ~7fps regardless of the requested framerate,
with zero error output — looks like a CPU problem, isn't. Already fixed in
`start.sh`; just don't remove it.

**The compositor's FIFO hand-off has an unresolved reconnect edge case.**
`demo/gstreamer/app.py` uses a named pipe (FIFO) to hand off between the
ingest ffmpeg process and the GStreamer pipeline, and again for egress.
FIFOs are reliable (no data loss, unlike the UDP transport tried first)
but have an edge case: if the writer process restarts, the reader can
stall silently with no error. A watchdog (`start_watchdog` in `app.py`)
detects "no frame drawn in `STALL_TIMEOUT_S`" and exits, relying on
`--restart=always` to recover — this **was observed to fire twice in a
5-minute soak test** on this dev VM and did recover cleanly both times,
but the underlying stall is contained, not fixed. An attempt to fix it
properly with TCP loopback sockets (unambiguous connect/disconnect
signaling, unlike a FIFO) introduced a *worse*, unresolved livelock and
was reverted — if you pick this up, that's the real remaining problem to
solve, not a new idea to reach for.

**Don't use `rtmp2sink`/`rtmp2src` (GStreamer) for anything that must
reach a real destination reliably.** They live in `gst-plugins-bad`,
which the GStreamer project's own README defines as "missing a good code
review, some documentation, a set of tests, a real live maintainer, or
some actual wide use... if the plug-ins break, you can't complain." Two
real bugs were hit in them this session (broken `location` URL parsing, a
silently-wrong `application`/`stream` path composition). `app.py`'s
ingest and egress both hand off to plain ffmpeg for the actual RTMP(S)
connections instead — keep it that way; don't "simplify" by switching
back to the native GStreamer RTMP elements.

**Podman VM clock drift (macOS specifically):** if the Mac sleeps, the
Podman machine's clock can drift several minutes behind the host, which
breaks TLS cert validation in builds (`current time is before <cert
validity start>`) and makes any timestamp-based log filtering (`--since`)
misleading. Fix: `podman machine stop && podman machine start`. If
container-build TLS errors mention a certificate not-yet-valid, check this
first before assuming a real cert problem.

## Why GStreamer over the alternatives (context, not action needed)

The underlying question both alternatives answer is "how do I add dynamic
switchable overlay layouts on top of a stream, OBS-style, without OBS?"
For a pure-web-app case with nothing non-web to composite, the simplest
answer beats both: the browser is already a compositor (`demo/scene/`'s
live chat panel already updates via DOM/JS with zero page reload, zero
extra process). This repo goes one step further than that anyway, using a
real second-stage compositor, because the goal was specifically to prove
out an OBS-free dynamic-layout pipeline — GStreamer was chosen over
Smelter after directly comparing both:
- **GStreamer**: ~25x lighter (1-4% CPU vs Smelter's 155% for the same
  overlay-compositing job), fully open source (LGPL, no usage caps). Cost:
  needed real low-level debugging to get right (see Known constraints).
- **Smelter**: nicer high-level API (React components, plain state = live
  scene updates), but licensed for real-time/live use only under specific
  caps (≤50 employees, <10k concurrent viewers/30-day, ≤50 machines) —
  past that, commercial licensing (~$1000+/mo).

## Security — explicitly NOT done yet

Per this project's own instruction, security hardening was deferred and
should be treated as still open:
- The control API above has **no authentication** — anyone who can reach
  the port can change the live layout.
- Secrets are handled via plain env files (`ai.env`), not a real secrets
  manager.
- No rate limiting, no TLS on any of the local control/HTTP surfaces.

Do not expose any of these ports to the open internet as-is.

## If the actual goal is a real YouTube production stream

This repo is a **local test-stream prototype**. For real production
(AlmaLinux GPU box, actual YouTube RTMP/RTMPS ingest, systemd supervision,
NVENC hardware encoding instead of software x264), read
`REMOTE_AGENT_PLAN.md` — it's a separate, more thorough deployment plan
covering the parts this repo doesn't (real GPU encoding, YouTube's actual
published encoder requirements, live-activation timing for new channels,
and a from-scratch AlmaLinux install sequence).
