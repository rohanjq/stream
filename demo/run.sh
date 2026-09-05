#!/usr/bin/env bash
# Build + run the local streaming demo entirely inside Podman.
# Nothing is installed on the host. View in a browser (no VLC needed):
#   HLS:    http://localhost:8888/live/
#   WebRTC: http://localhost:8889/live   (lower latency)
set -euo pipefail
cd "$(dirname "$0")"

IMG=ytstream-demo
POD=ytdemo

echo "==> building app image"
podman build -t "$IMG" -f Containerfile .

echo "==> (re)creating pod"
podman pod rm -f "$POD" >/dev/null 2>&1 || true
podman pod create --name "$POD" \
  -p 8080:8080 \
  -p 1935:1935 \
  -p 8888:8888 \
  -p 8889:8889 >/dev/null

echo "==> starting MediaMTX (local RTMP/HLS/WebRTC server)"
podman run -d --pod "$POD" --name "${POD}-mtx" \
  docker.io/bluenviron/mediamtx:latest >/dev/null

echo "==> starting streaming app (scene + chat AI + ffmpeg)"
podman run -d --pod "$POD" --name "${POD}-app" --shm-size=512m \
  --env-file ai.env "$IMG" >/dev/null

cat <<EOF

==> up. Two ways to watch:

   1) The scene directly (instant, best for this text demo):
        http://localhost:8080/

   2) The encoded YouTube-style stream (via local server):
        http://localhost:8888/live/     (HLS, ~5-10s latency)
        ffplay rtmp://localhost:1935/live   (near-zero latency)

   AI chat log:  podman exec ${POD}-app cat /opt/app/logs/replies.log
   App logs:     podman logs -f ${POD}-app
   Stop:         podman pod rm -f ${POD}
EOF
