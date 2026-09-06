#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
compose_command
cd "$PROJECT_ROOT"
if (( $# )); then
  "${COMPOSE[@]}" logs -f --tail=200 "$@"
else
  "${COMPOSE[@]}" logs -f --tail=200
fi
