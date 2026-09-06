#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

errors=0
for command_name in curl openssl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "missing: $command_name" >&2
    errors=$((errors + 1))
  fi
done

compose_command
echo "compose: ${COMPOSE[*]}"

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  echo "missing: .env (copy .env.example and fill in secrets)" >&2
  errors=$((errors + 1))
else
  mode="$(stat -c '%a' "$PROJECT_ROOT/.env" 2>/dev/null || stat -f '%Lp' "$PROJECT_ROOT/.env")"
  if [[ "$mode" != "600" ]]; then
    echo "warning: .env mode is $mode; recommended: chmod 600 .env" >&2
  fi
  if grep -q 'replace-with-stream-key' "$PROJECT_ROOT/.env"; then
    echo "invalid: YOUTUBE_RTMP_URL still contains the example value" >&2
    errors=$((errors + 1))
  fi
fi

if [[ ! -f "$PROJECT_ROOT/demo/gstreamer/mediamtx.yml" ]]; then
  echo "missing: demo/gstreamer/mediamtx.yml" >&2
  errors=$((errors + 1))
fi

if (( errors > 0 )); then
  echo "doctor: $errors blocking issue(s)" >&2
  exit 1
fi
echo "doctor: prerequisites look good"
