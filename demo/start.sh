#!/usr/bin/env bash
set -euo pipefail

: "${WIDTH:=1280}" "${HEIGHT:=720}" "${FPS:=24}" "${VBITRATE:=2500k}"
: "${RTMP_TARGET:=rtmp://127.0.0.1:1935/live}" "${SCENE_PORT:=8080}"
export DISPLAY=:99 HOME=/root
export XDG_RUNTIME_DIR=/tmp/xdgr
mkdir -p "$XDG_RUNTIME_DIR/pulse"; chmod 700 "$XDG_RUNTIME_DIR"

# Idempotent restart: this container's filesystem (and any leftover
# processes/locks in it) survives a `podman restart` / crash-restart, so
# clear anything from a previous run before (re)starting services.
pkill -x ffmpeg 2>/dev/null || true
pkill -f scene_server.py 2>/dev/null || true

# ---- audio: PulseAudio null sink (TTS -> sink -> ffmpeg -> stream) ----
# Idempotent: a container restart reuses this filesystem, so clear any stale
# daemon/socket from a previous run before starting a fresh one.
pulseaudio --kill 2>/dev/null || true
rm -rf "$XDG_RUNTIME_DIR/pulse"; mkdir -p "$XDG_RUNTIME_DIR/pulse"
sleep 1
echo "[start] pulseaudio"
pulseaudio -D --exit-idle-time=-1 -n \
  --load="module-native-protocol-unix socket=$XDG_RUNTIME_DIR/pulse/native" \
  --load="module-null-sink sink_name=ytsink sink_properties=device.description=ytsink" \
  2>/dev/null || true
export PULSE_SERVER="unix:$XDG_RUNTIME_DIR/pulse/native"
sleep 1
pactl set-default-sink ytsink 2>/dev/null || true
if pactl list short sinks 2>/dev/null | grep -q ytsink; then AUDIO_OK=1; else AUDIO_OK=0; fi
echo "[start] audio sink ok=$AUDIO_OK"

# ---- scene server (serves the composite + /charts app + /api) ----
echo "[start] scene_server on :${SCENE_PORT}"
python3 /opt/app/scene_server.py &
sleep 1
# No ambient chat worker — questions come from the user via /ask.html or /api/ask.

# ---- virtual display + browser ----
# Idempotent: clear any stale lock/socket from a previous run in this same
# container filesystem (podman restart reuses it; Xvfb won't rebind otherwise).
pkill -x Xvfb 2>/dev/null || true
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
echo "[start] Xvfb ${WIDTH}x${HEIGHT}"
Xvfb :99 -screen 0 "${WIDTH}x${HEIGHT}x24" -nolisten tcp &
sleep 2
pkill -x chromium 2>/dev/null || true
rm -f "$HOME/.config/chromium/SingletonLock" "$HOME/.config/chromium/SingletonCookie" "$HOME/.config/chromium/SingletonSocket"
echo "[start] chromium kiosk"
chromium \
  --kiosk --no-sandbox --disable-dev-shm-usage \
  --enable-unsafe-swiftshader --use-gl=swiftshader \
  --no-first-run --disable-features=Translate,TranslateUI \
  --autoplay-policy=no-user-gesture-required \
  --window-size="${WIDTH},${HEIGHT}" --window-position=0,0 \
  "http://127.0.0.1:${SCENE_PORT}/" &
sleep 5

# ---- encode -> RTMP (auto-restart) ----
echo "[start] ffmpeg -> ${RTMP_TARGET} (audio_ok=$AUDIO_OK)"
while true; do
  if [ "$AUDIO_OK" = "1" ]; then
    AUDIO_IN=(-f pulse -i ytsink.monitor)
  else
    AUDIO_IN=(-f lavfi -i anullsrc=r=44100:cl=stereo)
  fi
  ffmpeg -hide_banner -loglevel warning \
    -thread_queue_size 1024 -f x11grab -video_size "${WIDTH}x${HEIGHT}" -framerate "${FPS}" -i :99 \
    -thread_queue_size 1024 "${AUDIO_IN[@]}" \
    -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
    -b:v "${VBITRATE}" -maxrate "${VBITRATE}" -bufsize "$(( ${VBITRATE%k} * 2 ))k" \
    -g "$(( FPS * 2 ))" \
    -c:a aac -b:a 128k -ar 44100 \
    -f flv "${RTMP_TARGET}" || true
  echo "[start] ffmpeg exited; retrying in 3s..."
  sleep 3
done
