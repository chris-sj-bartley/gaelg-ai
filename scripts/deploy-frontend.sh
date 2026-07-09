#!/usr/bin/env bash
#
# deploy-frontend.sh — sync the repo's frontend/ to (or from) the public
# frontend server over SSH.
#
# The frontend is a static single-page app (frontend/index.html + privacy.html
# + static assets). The repo is the source of truth; this script copies it to
# the server that actually serves it to the public.
#
# Config (env vars, or put them in etc/frontend-deploy.env — see below):
#   FRONTEND_DEPLOY_TARGET   user@host of the frontend server        (required)
#   FRONTEND_DEPLOY_PATH     web root on that server, e.g. /var/www/manx/  (required)
#   FRONTEND_DEPLOY_SSH_PORT ssh port                                 (default 22)
#   FRONTEND_DEPLOY_SSH_KEY  path to ssh private key                  (optional)
#
# Usage:
#   scripts/deploy-frontend.sh            # deploy repo frontend/ -> server (asks to confirm)
#   scripts/deploy-frontend.sh --dry-run  # show what would change, transfer nothing
#   scripts/deploy-frontend.sh --pull     # RECONCILE: copy live server -> repo frontend/
#   scripts/deploy-frontend.sh --yes      # skip the confirmation prompt (for CI)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="$REPO_ROOT/frontend/"
CONFIG_FILE="${FRONTEND_DEPLOY_CONFIG:-/exp/exp1/acp24csb/etc/frontend-deploy.env}"

# Load config file if present (keeps host/path out of the repo if desired).
# shellcheck disable=SC1090
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

DRY_RUN=""
PULL=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="--dry-run" ;;
    --pull)    PULL=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

: "${FRONTEND_DEPLOY_TARGET:?set FRONTEND_DEPLOY_TARGET (user@host) — see $CONFIG_FILE}"
: "${FRONTEND_DEPLOY_PATH:?set FRONTEND_DEPLOY_PATH (web root on server)}"
SSH_PORT="${FRONTEND_DEPLOY_SSH_PORT:-22}"

SSH_CMD="ssh -p $SSH_PORT -o StrictHostKeyChecking=accept-new"
[ -n "${FRONTEND_DEPLOY_SSH_KEY:-}" ] && SSH_CMD="$SSH_CMD -i $FRONTEND_DEPLOY_SSH_KEY"

REMOTE="${FRONTEND_DEPLOY_TARGET}:${FRONTEND_DEPLOY_PATH%/}/"

# The frontend web root on the server holds more than the app: TLS keys
# (cert.pem/key.pem), backups (old_*), an issues/ dir and notes (*.md). These
# must NEVER be synced — especially key.pem must never land in the git repo on a
# --pull. So we exclude them in BOTH directions and only move the app source
# (index.html, privacy.html, static/).
EXCLUDES=(
  --include='/blog/***'  # blog posts (incl. *.md) MUST ship — keep before *.md exclude
  --exclude='*.pem'      # cert.pem, key.pem — TLS material, never in repo
  --exclude='old_*'      # old_index.html and other backups
  --exclude='*.md'       # FRONTEND_CHANGES_REQUIRED.md etc. (server-side notes)
  --exclude='issues'     # server-side issues dir
  --exclude='.git*'
)

# rsync flags: archive, compress, checksum (robust over time skew), itemise changes.
# --delete is intentionally NOT set so a deploy never removes files the server
# may legitimately have (e.g. server-specific assets). Add it deliberately if wanted.
RSYNC_FLAGS=(-az --checksum --itemize-changes --human-readable "${EXCLUDES[@]}" -e "$SSH_CMD")

if [ "$PULL" -eq 1 ]; then
  SRC="$REMOTE"; DST="$LOCAL_DIR"
  echo ">>> RECONCILE (pull): $SRC  ->  $DST"
  echo "    This overwrites repo frontend/ with the live server copy. Review with git diff after."
else
  [ -f "$LOCAL_DIR/index.html" ] || { echo "no $LOCAL_DIR/index.html — aborting"; exit 1; }
  SRC="$LOCAL_DIR"; DST="$REMOTE"
  echo ">>> DEPLOY (push): $SRC  ->  $DST"
fi

if [ -z "$DRY_RUN" ] && [ "$ASSUME_YES" -ne 1 ]; then
  echo -n "Proceed? [y/N] "; read -r ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "aborted."; exit 0; }
fi

set -x
rsync "${RSYNC_FLAGS[@]}" $DRY_RUN "$SRC" "$DST"
