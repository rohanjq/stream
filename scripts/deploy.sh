#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

require_secrets
compose_command
cd "$PROJECT_ROOT"

"${COMPOSE[@]}" config >/dev/null
"${COMPOSE[@]}" up -d --build

echo "Waiting for the scene API..."
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    echo "deployment healthy"
    echo "scene:   http://127.0.0.1:8080/"
    echo "preview: http://127.0.0.1:8888/raw/"
    exit 0
  fi
  sleep 2
done

echo "deployment did not become healthy within 120 seconds" >&2
"${COMPOSE[@]}" ps >&2 || true
exit 1
