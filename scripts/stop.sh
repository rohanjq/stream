#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
compose_command
cd "$PROJECT_ROOT"
# Named volumes are intentionally retained. Use `compose down -v` manually
# only when you explicitly want to erase persisted music/control state.
"${COMPOSE[@]}" down
