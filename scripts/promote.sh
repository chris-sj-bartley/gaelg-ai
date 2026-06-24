#!/usr/bin/env bash
#
# promote.sh — promote the current master to production.
#
# The staging -> master merge is done by reviewing & merging the PR on GitHub
# (that is the human approval gate). This script does NOT merge or push; it:
#   1. smoke-tests staging (:8001)
#   2. fast-forwards the prod worktree to origin/master and restarts prod (:8000)
#   3. smoke-tests prod
#
# So the full flow is:
#   land change on `staging` -> deploy-backend.sh staging -> test :8001
#   -> merge the staging->master PR on GitHub -> promote.sh
#
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="/exp/exp1/acp24csb/web_platform"
STAGING_URL="http://127.0.0.1:8001"
PROD_URL="http://143.167.8.81:8000"

echo "==> Smoke-testing staging ($STAGING_URL)..."
"$HERE/smoke.sh" "$STAGING_URL"

echo "==> Checking master is in sync with origin..."
git -C "$REPO" fetch -q origin
LOCAL=$(git -C "$REPO" rev-parse master)
REMOTE=$(git -C "$REPO" rev-parse origin/master)
if [ "$LOCAL" != "$REMOTE" ]; then
  echo "    note: local master ($(git -C "$REPO" rev-parse --short master)) != origin/master ($(git -C "$REPO" rev-parse --short origin/master))."
  echo "    deploy-backend.sh will fast-forward to origin/master."
fi

echo "==> Deploying master to prod..."
"$HERE/deploy-backend.sh" prod

echo "==> Smoke-testing prod ($PROD_URL)..."
"$HERE/smoke.sh" "$PROD_URL"

echo "==> Promotion complete. Prod now at $(git -C "$REPO" rev-parse --short master)."
