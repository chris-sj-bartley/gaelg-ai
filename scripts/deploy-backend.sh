#!/usr/bin/env bash
#
# deploy-backend.sh staging|prod
#
# Pulls the branch for the target env into its worktree and restarts its
# service. staging and prod run as different systemd units, from different
# worktrees / branches:
#
#   staging -> web_platform_staging (branch: staging) -> manx-api@staging (:8001)
#   prod    -> web_platform         (branch: master)  -> manx-tts          (:8000)
#
set -euo pipefail
ENV="${1:?usage: deploy-backend.sh staging|prod}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

case "$ENV" in
  staging) WORKDIR="/exp/exp1/acp24csb/web_platform_staging"; SERVICE="manx-api@staging" ;;
  prod)    WORKDIR="/exp/exp1/acp24csb/web_platform";         SERVICE="manx-tts" ;;
  *) echo "usage: deploy-backend.sh staging|prod" >&2; exit 2 ;;
esac

cd "$WORKDIR"

# Pull only if the branch has an upstream (staging may be local-only until pushed).
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  echo "==> git pull --ff-only ($(git rev-parse --abbrev-ref HEAD))"
  git pull --ff-only
else
  echo "==> no upstream for $(git rev-parse --abbrev-ref HEAD); deploying local state"
fi

# Deps are managed in the shared venv; only run pip if a requirements file exists.
if [ -f requirements.txt ]; then
  echo "==> installing requirements"
  /exp/exp1/acp24csb/venv/bin/pip install -q -r requirements.txt
fi

echo "==> restarting $SERVICE"
systemctl --user restart "$SERVICE"
echo "==> deployed $ENV: $SERVICE now at $(git rev-parse --short HEAD) in $WORKDIR"
