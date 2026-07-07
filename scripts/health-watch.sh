#!/bin/bash
# health-watch.sh — escalation watcher for the prod API (cron: every 5 min).
#
# healthcheck.sh (1-min cron) already restarts the service and pings Telegram.
# This script handles the case where restarting *doesn't* fix it: if prod is
# unhealthy on two consecutive 5-min runs (so at least one restart cycle has
# failed in between), it files a Linear incident on the CHR board so the fix
# gets tracked/actioned. Deduped: no new issue while an incident is open.

HEALTH_URL="http://143.167.8.81:8000/health"
REPO="/exp/exp1/acp24csb/web_platform"
STATE_FILE="$REPO/logs/health-watch.state"
LOG_FILE="$REPO/logs/health-watch.log"
PYTHON="/exp/exp1/acp24csb/venv/bin/python3"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

log() { echo "$TIMESTAMP $1" >> "$LOG_FILE"; }

RESPONSE=$(curl -s --max-time 10 "$HEALTH_URL" 2>/dev/null)
STATUS=$(echo "$RESPONSE" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

if [ "$STATUS" = "healthy" ]; then
    echo "healthy" > "$STATE_FILE"
    exit 0
fi

PREVIOUS=$(cat "$STATE_FILE" 2>/dev/null || echo "healthy")
echo "unhealthy" > "$STATE_FILE"

if [ -z "$RESPONSE" ]; then
    DETAIL="no response from $HEALTH_URL"
else
    DETAIL="$RESPONSE"
fi
log "WARNING: prod unhealthy (previous run: $PREVIOUS) — $DETAIL"

# First bad run: healthcheck.sh's restart may still fix it. Wait for the next.
[ "$PREVIOUS" = "healthy" ] && exit 0

RESULT=$("$PYTHON" "$REPO/scripts/linear_issue.py" \
    --title "Incident: prod API unhealthy despite restarts ($TIMESTAMP)" \
    --labels incident,agent-ready \
    --dedupe-label incident <<EOF
Prod API at $HEALTH_URL has been unhealthy for two consecutive 5-minute
checks. healthcheck.sh restarts the service every minute when unhealthy, so
restarting is not fixing this.

Last /health response:
\`\`\`
$DETAIL
\`\`\`

Where to look:
- \`journalctl --user -u manx-tts -n 200\` on cassini
- \`$REPO/logs/healthcheck.log\` (restart history)
- \`$REPO/logs/health-watch.log\`
EOF
)
log "INFO: escalation -> $RESULT"
