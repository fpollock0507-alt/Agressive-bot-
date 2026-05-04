#!/usr/bin/env bash
# Generate EOD report + 30-day dashboard, then push to GitHub.
# Invoked by cron at 16:05 ET (06:05 AEST next day) on weekdays.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

python -m bot.main eod
