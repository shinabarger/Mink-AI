#!/bin/bash
# Starts Mink. Run with: bash start.command
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "First time setup: installing uv, a small Python installer."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "Starting Mink. The first launch installs everything it needs and can take a few minutes."
# caffeinate keeps the Mac awake while Mink runs, so long transcriptions aren't paused by sleep.
exec caffeinate -i uv run --python 3.12 python -m mink
