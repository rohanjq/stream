#!/usr/bin/env bash
set -euo pipefail
echo "demo/run.sh is retained for compatibility; using the root deployment stack."
exec "$(cd "$(dirname "$0")/.." && pwd)/scripts/deploy.sh" "$@"
