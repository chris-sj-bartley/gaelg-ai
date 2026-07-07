#!/bin/bash
# housekeeping.sh — weekly maintenance report + light cleanup (cron: Mon 09:00).
#
# - Reports disk usage (root FS + /exp/exp1), GPU state and outdated pip
#   packages to logs/housekeeping.log.
# - Removes mkdtemp leftovers (tmp*/ in the repo root) older than 7 days.
# - If packages are outdated, files a dep-bump issue on the CHR Linear board
#   (deduped: no new issue while one is open) so an agent can propose the bump.

REPO="/exp/exp1/acp24csb/web_platform"
VENV="/exp/exp1/acp24csb/venv"
LOG_FILE="$REPO/logs/housekeeping.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

log() { echo "$1" >> "$LOG_FILE"; }

log "=== housekeeping run $TIMESTAMP ==="

# --- disk ---
DISK=$(df -h / /exp/exp1 2>/dev/null | grep -v '^Filesystem')
log "disk:"
log "$DISK"

# --- GPU ---
GPU=$(nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu \
      --format=csv,noheader 2>/dev/null || echo "nvidia-smi unavailable")
log "gpu: $GPU"

# --- mkdtemp leftovers in the repo root (created by the backend) ---
REMOVED=$(find "$REPO" -maxdepth 1 -type d -name 'tmp??*' -mtime +7 \
          -exec rm -rf {} + -print 2>/dev/null | wc -l)
log "cleanup: removed $REMOVED stale tmp*/ dirs (>7 days old)"

# --- outdated packages ---
OUTDATED=$("$VENV/bin/pip" list --outdated --format=columns 2>/dev/null | grep -v '^\[')
COUNT=$(echo "$OUTDATED" | tail -n +3 | grep -c . )
log "outdated packages: $COUNT"
[ "$COUNT" -gt 0 ] && log "$OUTDATED"

if [ "$COUNT" -gt 0 ]; then
    RESULT=$("$VENV/bin/python3" "$REPO/scripts/linear_issue.py" \
        --title "Weekly dep scan: $COUNT outdated packages ($(date '+%Y-%m-%d'))" \
        --labels dep-bump \
        --dedupe-label dep-bump <<EOF
Weekly \`pip list --outdated\` in the shared venv ($VENV):

\`\`\`
$OUTDATED
\`\`\`

Disk:
\`\`\`
$DISK
\`\`\`

Propose bumps via PR only (propose-only mode) — pin exact versions in
requirements.txt and confirm \`scripts/smoke.sh http://127.0.0.1:8001\` passes
on staging before requesting review. Note torch/CUDA pins are load-bearing on
cassini; do not bump those without checking GPU compat.
EOF
    )
    log "linear: $RESULT"
fi

log ""
