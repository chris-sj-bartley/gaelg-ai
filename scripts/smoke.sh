#!/usr/bin/env bash
set -euo pipefail
BASE="${1:?usage: smoke.sh <base-url> [curl-extra-args]}"
shift
EXTRA=("$@")   # e.g. -u user:pass for staging basic-auth

# Buffer the page before grepping: with pipefail, `curl | grep -q` fails once the
# page outgrows one pipe buffer (grep exits at first match, curl gets SIGPIPE).
homepage=$(curl -fsS "${EXTRA[@]}" "$BASE/")
grep -qi "manx" <<<"$homepage" || { echo "homepage failed"; exit 1; }

resp=$(curl -fsS "${EXTRA[@]}" "$BASE/health")
echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d['status'] not in ('healthy', 'unhealthy'):
    sys.exit(1)
errors = d.get('errors') or {}
# on CPU staging, GPU models may be slow/absent — only fail if nothing loaded
loaded = [k for k,v in d['models'].items() if v]
if not loaded:
    print('smoke FAILED: no models loaded')
    sys.exit(1)
print(f'smoke OK  models={loaded}  errors={list(errors.keys()) or None}')
"
