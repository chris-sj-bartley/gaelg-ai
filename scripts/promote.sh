#!/usr/bin/env bash
# Promote staging → prod after smoke test passes.
set -euo pipefail

STAGING_URL="http://127.0.0.1:8001"
PROD_URL="http://143.167.8.81:8000"

echo "==> Smoke-testing staging..."
"$(dirname "$0")/smoke.sh" "$STAGING_URL"

echo "==> Merging staging → master and deploying prod..."
git -C /exp/exp1/acp24csb/web_platform fetch origin
git -C /exp/exp1/acp24csb/web_platform merge --ff-only origin/staging
git -C /exp/exp1/acp24csb/web_platform push origin master

"$(dirname "$0")/deploy-backend.sh" prod

echo "==> Smoke-testing prod..."
"$(dirname "$0")/smoke.sh" "$PROD_URL"

echo "==> Promotion complete."
