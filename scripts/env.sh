# Sourced by the other scripts: tool paths and shared settings.
export PATH="$HOME/.foundry/bin:$PATH"
if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
export PY
export PYTHONPATH=.
export RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
export CHAIN_ID="${CHAIN_ID:-31337}"
# Public Anvil test key #0 (admin/deployer). Local chain only - never use on a real network.
ANVIL_KEY0=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
mkdir -p var
