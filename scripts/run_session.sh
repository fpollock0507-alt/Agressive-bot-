#!/usr/bin/env bash
# Run one trading session. Invoked by cron at 23:30 AEST on weekdays.
# Wrapped in `caffeinate` so the Mac can't sleep mid-session and orphan an
# open 0DTE option that would expire worthless overnight.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

# -i prevent system idle sleep
# -m prevent disk idle sleep
# -s prevent system sleep when on AC power
exec caffeinate -ims python -m bot.main session
