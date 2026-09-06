#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
compose_command
cd "$PROJECT_ROOT"
"${COMPOSE[@]}" ps
echo
curl -fsS http://127.0.0.1:8080/api/health || true
echo
curl -fsS "http://127.0.0.1:${OPERATOR_HOST_PORT:-8082}/healthz" || true
echo
