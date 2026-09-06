#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

compose_command() {
  if command -v podman-compose >/dev/null 2>&1; then
    COMPOSE=(podman-compose -f "$PROJECT_ROOT/compose.yaml")
  elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
    COMPOSE=(podman compose -f "$PROJECT_ROOT/compose.yaml")
  elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose -f "$PROJECT_ROOT/compose.yaml")
  else
    echo "error: install Podman Compose or Docker Compose" >&2
    exit 1
  fi
}

require_secrets() {
  if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
    echo "error: .env is missing; run: cp .env.example .env" >&2
    exit 1
  fi
  if ! grep -Eq '^YOUTUBE_RTMP_URL=rtmps?://.+/[^[:space:]]+' "$PROJECT_ROOT/.env" || \
     grep -q 'replace-with-stream-key' "$PROJECT_ROOT/.env"; then
    echo "error: set YOUTUBE_RTMP_URL in .env" >&2
    exit 1
  fi
  if ! grep -Eq '^CONTROL_TOKEN=.{32,}$' "$PROJECT_ROOT/.env" || \
     grep -q '^CONTROL_TOKEN=replace-' "$PROJECT_ROOT/.env"; then
    echo "error: set CONTROL_TOKEN to at least 32 characters in .env" >&2
    exit 1
  fi
}
