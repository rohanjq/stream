# Handoff: Headless YouTube Live Stream + AI Chat Character on AlmaLinux

> Paste this whole document to OpenCode/DeepSeek running on the AlmaLinux box.
> It is the full plan and instruction set. Ask Rohan for any inputs marked **INPUT NEEDED**.

## 0. What you're building

A headless (no monitor) AlmaLinux machine that:
1. Renders a **web scene** (charting web app — for now just a live clock + AI avatar + caption overlay) in a browser on a **virtual display**.
2. Captures that virtual display + a virtual audio sink and **streams it to YouTube Live via RTMP**, starting in **Private** visibility.
3. Runs a **chat worker** that reads YouTube live chat, sends each comment to a **DeepSeek endpoint (Azure AI Foundry)**, gets a reply, **speaks it via local TTS into the stream audio**, and **logs every reply to a file**.
4. AI character for phase 1 = a **static avatar image** (no lipsync). Lipsync is a later phase.

Machine has a **GPU** → use **NVENC** hardware encoding. Start at **low resolution (720p)**.

### Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │  AlmaLinux host (headless, has GPU)           │
                 │                                               │
 Web scene ──►   │  Chromium (kiosk) ─► Xvfb :99 (virtual display)│
 (clock+avatar   │        ▲                     │                 │
  +captions)     │        │ websocket           │ x11grab (video) │
                 │  Caption/scene server        ▼                 │
                 │                        FFmpeg (NVENC) ─ RTMP ─► YouTube Live
 Chat worker ──► │  YouTube Data API poll        ▲   (Private broadcast)
                 │    │                          │ pulse (audio)   │
                 │    ├─► DeepSeek (Azure) reply  │                 │
                 │    ├─► log file                │                 │
                 │    ├─► Piper TTS ─► paplay ─► PulseAudio null sink
                 │    └─► push caption ─► scene server (websocket)  │
                 └─────────────────────────────────────────────┘
```

Everything runs as **systemd services** so it survives disconnects and restarts.

---

## 1. Inputs to collect from Rohan (ask when you reach the step)

1. **INPUT NEEDED — YouTube RTMP stream key** — from YouTube Studio → Go Live → Stream. (Live streaming must be enabled first; can take up to 24h for a new channel.)
2. **INPUT NEEDED — DeepSeek Azure AI Foundry**: (a) full endpoint URL, (b) API key, (c) auth header style, (d) model/deployment name. Azure Foundry uses one of:
   - `api-key: <KEY>` with URL like `https://<res>.services.ai.azure.com/models/chat/completions?api-version=2024-05-01-preview`, model name in body, **or**
   - `Authorization: Bearer <KEY>` with a serverless URL like `https://<name>.<region>.models.ai.azure.com/v1/chat/completions`.
3. **INPUT NEEDED — YouTube Data API OAuth** (for reading live chat): Rohan creates a Google Cloud project, enables **YouTube Data API v3**, makes an **OAuth Desktop client**, gives you `client_id` + `client_secret`. You run a one-time OAuth flow for a refresh token. (Defer with the file stub in Phase 1 if not ready.)
4. **Target resolution/FPS** — default **1280x720 @ 30fps**, `2500k`. Confirm or override.
5. **INPUT NEEDED — Avatar image** — a PNG for the character, or use a placeholder.

**First, report hardware back to Rohan:**
```bash
nvidia-smi 2>/dev/null || echo "no NVIDIA GPU"
lscpu | egrep 'Model name|^CPU\(s\)'
free -h
cat /etc/almalinux-release
ffmpeg -encoders 2>/dev/null | grep nvenc || echo "nvenc not available yet"
```

---

## 2. Rohan's manual YouTube setup (browser — cannot be automated)

1. Create Google account + YouTube channel.
2. Verify channel by phone at youtube.com/verify, then **enable Live Streaming**. **First live activation can take up to 24 hours.**
3. Studio → **Create → Go Live → Stream** tab → visibility **Private** → copy **Stream key** and **Stream URL** (`rtmp://a.rtmp.youtube.com/live2`).
4. Private broadcasts restrict chat visibility to invited viewers; for painless chat testing use **Unlisted** during dev, switch to Private/Public later.

---

## 3. Install tooling (AlmaLinux 9)

```bash
# Repos: EPEL + CRB + RPM Fusion (for ffmpeg)
sudo dnf install -y epel-release
sudo dnf config-manager --set-enabled crb
sudo dnf install -y https://download1.rpmfusion.org/free/el/rpmfusion-free-release-9.noarch.rpm

sudo dnf install -y \
  ffmpeg xorg-x11-server-Xvfb chromium \
  pulseaudio pulseaudio-utils alsa-utils \
  nodejs python3 python3-pip \
  dejavu-sans-fonts liberation-fonts google-noto-emoji-fonts \
  wget tar xdotool

which Xvfb ffmpeg pulseaudio paplay python3 node
command -v chromium-browser || command -v chromium
ffmpeg -encoders 2>/dev/null | egrep 'nvenc|libx264'
nvidia-smi
```
- Chromium binary is `chromium-browser` or `chromium`. If unavailable, install `google-chrome-stable` and use `google-chrome`.
- Ensure NVIDIA driver is installed and `nvidia-smi` works before relying on NVENC.

### Piper TTS (local, offline)
```bash
sudo mkdir -p /opt/piper && cd /opt/piper
wget -O piper.tar.gz https://github.com/rhasspy/piper/releases/latest/download/piper_linux_x86_64.tar.gz
tar -xzf piper.tar.gz     # -> ./piper/piper
wget -P /opt/piper/voices https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
wget -P /opt/piper/voices https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
echo "hello world, stream test" | /opt/piper/piper/piper -m /opt/piper/voices/en_US-amy-medium.onnx -f /tmp/t.wav && ls -l /tmp/t.wav
```

### Python deps
```bash
sudo pip3 install --upgrade google-api-python-client google-auth-oauthlib google-auth-httplib2 requests websockets
```

---

## 4. Project layout — `/opt/ytstream`

```
/opt/ytstream/
  .env                 # secrets/config (chmod 600)
  scene/index.html     # web scene (clock + avatar + captions)
  scene/avatar.png
  scene_server.py      # serves scene/ + websocket for captions
  streamer.sh          # Xvfb + pulse + chromium + ffmpeg pipeline
  chat_worker.py       # YouTube chat -> DeepSeek -> TTS -> log -> caption
  fake_chat.txt        # phase-1 stub chat input
  logs/replies.log
```

### `.env` (fill from inputs, `chmod 600`)
```ini
# YouTube
YT_STREAM_KEY=xxxx-xxxx-xxxx-xxxx
YT_RTMP_URL=rtmp://a.rtmp.youtube.com/live2

# Video (low-res start, GPU encode)
WIDTH=1280
HEIGHT=720
FPS=30
VBITRATE=2500k
ENCODER=h264_nvenc

# DeepSeek (Azure AI Foundry)
DEEPSEEK_URL=
DEEPSEEK_KEY=
DEEPSEEK_MODEL=
DEEPSEEK_AUTH=api-key      # "api-key" or "bearer"

# YouTube Data API OAuth (for chat)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_TOKEN_FILE=/opt/ytstream/yt_token.json

# Scene
SCENE_URL=http://localhost:8080/
CAPTION_WS=ws://localhost:8765

# Phase-1 chat stub (1 = read fake_chat.txt instead of YouTube)
CHAT_STUB=1
```

---

## 5. Web scene — `scene/index.html`

Full-screen dark page: big live clock, avatar image, caption bar updated over websocket. Charting web app slots in here later.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{margin:0;height:100%;background:#0b0f14;color:#e6edf3;
    font-family:'Liberation Sans',sans-serif;overflow:hidden}
  #clock{position:absolute;top:40px;left:60px;font-size:120px;font-variant-numeric:tabular-nums}
  #label{position:absolute;top:180px;left:64px;font-size:28px;color:#8b98a5}
  #avatar{position:absolute;right:40px;bottom:120px;height:420px}
  #caption{position:absolute;left:0;right:0;bottom:0;padding:20px 40px;
    background:rgba(0,0,0,.55);font-size:34px;min-height:50px}
  #who{color:#5eb3ff;margin-right:12px}
</style></head><body>
  <div id="clock">--:--:--</div>
  <div id="label">LIVE • charting placeholder</div>
  <img id="avatar" src="avatar.png" alt="AI host">
  <div id="caption"><span id="who"></span><span id="msg"></span></div>
<script>
  const c=document.getElementById('clock');
  setInterval(()=>{c.textContent=new Date().toLocaleTimeString('en-GB');},250);
  function connect(){
    const ws=new WebSocket("ws://localhost:8765");
    ws.onmessage=e=>{const d=JSON.parse(e.data);
      document.getElementById('who').textContent=d.who?d.who+':':'';
      document.getElementById('msg').textContent=d.text||'';};
    ws.onclose=()=>setTimeout(connect,2000);
  }
  connect();
</script></body></html>
```

`scene_server.py`: serve `scene/` on :8080 (http.server in a thread) + a `websockets` hub on :8765 that rebroadcasts caption JSON `{who, text}` to all browser clients. The chat worker connects as a client and sends captions.

---

## 6. Streaming pipeline — `streamer.sh` (NVENC)

```bash
#!/usr/bin/env bash
set -euo pipefail
source /opt/ytstream/.env
export DISPLAY=:99

# 1) Virtual display
Xvfb :99 -screen 0 ${WIDTH}x${HEIGHT}x24 -nolisten tcp &
sleep 2

# 2) Virtual audio
export PULSE_SERVER=unix:/run/user/$(id -u)/pulse/native 2>/dev/null || true
pulseaudio --start --exit-idle-time=-1 || true
pactl load-module module-null-sink sink_name=ytsink sink_properties=device.description=ytsink || true
pactl set-default-sink ytsink

# 3) Browser renders the scene
CHROME=$(command -v chromium-browser || command -v chromium || command -v google-chrome)
"$CHROME" --kiosk --no-first-run --no-sandbox --disable-gpu \
  --window-size=${WIDTH},${HEIGHT} --autoplay-policy=no-user-gesture-required \
  --window-position=0,0 "$SCENE_URL" &
sleep 5

# 4) Encode display+audio -> RTMP (NVENC) — Private broadcast
ffmpeg -f x11grab -video_size ${WIDTH}x${HEIGHT} -framerate ${FPS} -i :99 \
  -f pulse -i ytsink.monitor \
  -c:v h264_nvenc -preset p4 -tune ll -rc cbr -pix_fmt yuv420p \
  -b:v ${VBITRATE} -maxrate ${VBITRATE} -bufsize $((${VBITRATE%k}*2))k \
  -g $((FPS*2)) \
  -c:a aac -b:a 128k -ar 44100 \
  -f flv "${YT_RTMP_URL}/${YT_STREAM_KEY}"
```
Notes:
- NVENC rate control: `-preset p1..p7` (p4 balanced), `-tune ll` low-latency, `-rc cbr` steady bitrate YouTube expects. (The x264 `-preset veryfast` does NOT apply to NVENC.)
- `-g` = 2×fps for the required constant keyframe interval.
- Keep Chromium `--disable-gpu` for now (avoids headless GPU-compositing quirks); NVENC still uses the GPU for encoding.
- TTS audio played via `paplay` lands in `ytsink`; ffmpeg reads `ytsink.monitor`, so speech is in the broadcast.

---

## 7. Chat worker — `chat_worker.py` (behavior spec)

Loop:
1. **Live chat id (real mode):** OAuth call `liveBroadcasts.list(part=snippet, broadcastStatus=active, mine=true)` → `snippet.liveChatId`. Retry until live.
2. **Poll** `liveChatMessages.list(liveChatId, part=snippet,authorDetails, pageToken)`; respect `pollingIntervalMillis`; track `nextPageToken`; skip seen + own messages.
3. Per new viewer message:
   - **DeepSeek call** (header by `DEEPSEEK_AUTH`):
     - `api-key` → header `{"api-key": KEY}`
     - `bearer` → header `{"Authorization": "Bearer "+KEY}`
     - Body: `{"model": DEEPSEEK_MODEL, "messages":[{"role":"system","content":"You are a friendly live-stream host. Reply in <=2 short sentences."},{"role":"user","content": <comment>}], "max_tokens":120, "temperature":0.7}`
   - **Log** to `logs/replies.log`: `TIMESTAMP | author: <comment> | AI: <reply>`  (phase-1 required "reply in log file").
   - **TTS**: `echo <reply> | /opt/piper/piper/piper -m <voice.onnx> -f /tmp/reply.wav` then `paplay /tmp/reply.wav`.
   - **Caption**: send `{"who": author, "text": reply}` to `CAPTION_WS`.
   - Serialize with a queue so replies don't overlap (await audio finish).
4. **Stub mode (`CHAT_STUB=1`)**: tail `/opt/ytstream/fake_chat.txt`; each new line is a viewer comment. Validates DeepSeek→TTS→log→caption with zero Google setup.

One-time OAuth (real mode): `InstalledAppFlow` with `client_id/secret`, scope `https://www.googleapis.com/auth/youtube.readonly`; save `yt_token.json`. (Posting replies back to chat later would need `youtube.force-ssl`.)

---

## 8. systemd services (headless, auto-restart)

Run as a dedicated user (or Rohan's user with `loginctl enable-linger <user>` so the PulseAudio user session persists). Three units, ordered:
- `ytscene.service` → `python3 /opt/ytstream/scene_server.py`
- `ytstream.service` → `/opt/ytstream/streamer.sh` (After=ytscene)
- `ytchat.service` → `python3 /opt/ytstream/chat_worker.py` (After=ytstream)

Each: `Restart=always`, `EnvironmentFile=/opt/ytstream/.env`, correct `User=`, and for display/audio units set `Environment=DISPLAY=:99` and `XDG_RUNTIME_DIR`.

---

## 9. Bring-up / test order (do NOT go straight to YouTube)

1. **Scene locally**: start `ytscene`; verify clock via headless screenshot `ffmpeg -f x11grab -frames 1 /tmp/shot.png`.
2. **Local recording first**: temporarily change ffmpeg output to `-t 20 /tmp/test.mp4`; confirm valid video+audio (play a TTS clip during capture).
3. **DeepSeek reachability**: `curl` the endpoint with a tiny payload; expect 200 + reply.
4. **Stub chat end-to-end**: `CHAT_STUB=1`, append a line to `fake_chat.txt`; confirm reply logged, TTS audible, caption shown.
5. **Go live PRIVATE**: set real `YT_STREAM_KEY`, restore RTMP output, start `ytstream`; Studio shows connected/live.
6. **Real chat**: finish OAuth, `CHAT_STUB=0`, post a test comment, confirm full loop.
7. Rohan flips Private→Public when happy.

---

## 10. Gotchas / flag to Rohan

- New channel: **24h live activation delay**.
- **Private** limits chat visibility; use **Unlisted** during dev if needed.
- If 720p is smooth, bump `.env` to 1920x1080 / 4500k later.
- Piper is CPU-only and fine; nicer voices swappable later.
- YouTube Data API has a daily quota; don't poll faster than `pollingIntervalMillis`.
- **Later phases (out of scope now):** lipsync/animated avatar, the real charting web app, and posting AI replies back into YouTube chat (needs `youtube.force-ssl` + quota care).

**When blocked, ask Rohan** for: stream key; DeepSeek URL/key/model/auth-style; Google OAuth client id/secret; avatar image; target resolution.
