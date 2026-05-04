#!/usr/bin/env bash
# Install cron entries for market open (9:30 ET) and EOD (16:05 ET).
# macOS BSD cron uses LOCAL time and ignores CRON_TZ — schedules below are
# Australia/Sydney AEST (UTC+10) translated from US Eastern.
# Update twice a year for DST drift.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SESSION_SCRIPT="$PROJECT_DIR/scripts/run_session.sh"
EOD_SCRIPT="$PROJECT_DIR/scripts/run_eod.sh"

chmod +x "$SESSION_SCRIPT" "$EOD_SCRIPT"

PROJECT_DIR_ESC="${PROJECT_DIR// /\\ }"
SESSION_SCRIPT_ESC="${SESSION_SCRIPT// /\\ }"
EOD_SCRIPT_ESC="${EOD_SCRIPT// /\\ }"

TMP="$(mktemp)"
crontab -l 2>/dev/null | grep -v "# aggressive-bot:" > "$TMP" || true

cat >> "$TMP" <<EOF
# aggressive-bot: market-open session
# US 9:30 ET = 23:30 AEST same day  → cron Mon-Fri at 23:30
# US 16:05 ET = 06:05 AEST next day → cron Tue-Sat at 06:05
30 23 * * 1-5 $SESSION_SCRIPT_ESC >> $PROJECT_DIR_ESC/logs/cron_session.log 2>&1 # aggressive-bot:
5 6 * * 2-6 $EOD_SCRIPT_ESC >> $PROJECT_DIR_ESC/logs/cron_eod.log 2>&1 # aggressive-bot:
EOF

crontab "$TMP"
rm "$TMP"

echo "Installed cron jobs. View with: crontab -l"
echo ""
echo "IMPORTANT: On macOS, cron needs Full Disk Access (you may already have"
echo "this set from the ORB bot — check)."
echo "  System Settings → Privacy & Security → Full Disk Access → /usr/sbin/cron"
