#!/usr/bin/env bash
# make demo: fresh local chain + attestor, then the four PRD scenarios headless.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
start=$(date +%s)
trap 'bash scripts/down.sh' EXIT
bash scripts/up.sh
$PY -m attestor.demo
echo "make demo finished in $(( $(date +%s) - start )) s"
