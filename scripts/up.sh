#!/usr/bin/env bash
# Start a fresh local stack: Anvil -> deploy contracts (demo installations) -> attestor.
# Leaves both running in the background; PIDs in var/*.pid. Stop with scripts/down.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
bash scripts/down.sh >/dev/null 2>&1 || true

anvil --silent --port 8545 > var/anvil.log 2>&1 &
echo $! > var/anvil.pid
for _ in $(seq 1 50); do cast block-number --rpc-url "$RPC_URL" >/dev/null 2>&1 && break; sleep 0.2; done
cast block-number --rpc-url "$RPC_URL" >/dev/null

# Attestor state lives in $DATA_DIR (default attestor/var). The ML-DSA keypair persists across runs;
# the sqlite store and the local IPFS store belong to one chain, so a fresh chain clears them.
export DATA_DIR="${DATA_DIR:-attestor/var}"
rm -rf "$DATA_DIR/ipfs" "$DATA_DIR/attestor.db"
ATTESTOR_PQ_KEY_HASH=$($PY -m attestor.keys | tr -d ' \r\n')
MODEL_VERSION_HASH=$(tr -d ' \r\n' < model/artefacts/model_version.txt)

(cd contracts && DEMO=1 MODEL_VERSION_HASH="$MODEL_VERSION_HASH" ATTESTOR_PQ_KEY_HASH="$ATTESTOR_PQ_KEY_HASH" \
  forge script script/Deploy.s.sol:Deploy --rpc-url "$RPC_URL" --private-key "$ANVIL_KEY0" --broadcast >../var/deploy.log 2>&1) \
  || { echo "deploy failed - see var/deploy.log"; tail -20 var/deploy.log; exit 1; }

$PY -m uvicorn attestor.app:app --host 127.0.0.1 --port 8000 > var/attestor.log 2>&1 &
echo $! > var/attestor.pid
for _ in $(seq 1 150); do curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 0.2; done
curl -sf http://127.0.0.1:8000/health || { echo "attestor failed - see var/attestor.log"; tail -30 var/attestor.log; exit 1; }
echo
echo "stack up: anvil :8545, attestor :8000"
