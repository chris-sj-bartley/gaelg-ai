#!/usr/bin/env bash
set -euo pipefail
ENV="${1:?usage: deploy-backend.sh staging|prod}"

WORKDIR="/exp/exp1/acp24csb/web_platform${ENV:+_$ENV}"
[[ "$ENV" == "prod" ]] && WORKDIR="/exp/exp1/acp24csb/web_platform"

cd "$WORKDIR"
git pull --ff-only
/exp/exp1/acp24csb/venv/bin/pip install -q -r requirements.txt
systemctl --user restart "manx-api@$ENV"
