# EmbedProof
#   make setup   one-time: Python venv + deps, Foundry libs + build, web deps
#   make demo    fresh Anvil chain + contracts + attestor, runs the 4 PRD scenarios headless
#   make up      same stack, left running for the web UI (stop with: make down)
#   make web     Next.js UI on http://localhost:3000 (demo personas, needs `make up`)
#   make test    contract, model and attestor tests
SHELL := bash
.ONESHELL:
.PHONY: setup demo up down web test test-contracts test-py coverage train deploy-testnet

setup:
	source scripts/env.sh
	command -v forge >/dev/null || { echo "Install Foundry first: https://book.getfoundry.sh/getting-started/installation"; exit 1; }
	[ -d .venv ] || uv venv --python 3.11 .venv || python3.11 -m venv .venv
	uv pip install --python $$PY -r requirements.txt || $$PY -m pip install -r requirements.txt
	cd contracts && forge build && cd ..
	$$PY contracts/script/export_abi.py
	cd web && pnpm install

demo:
	bash scripts/demo.sh

up:
	bash scripts/up.sh

down:
	bash scripts/down.sh

web:
	cd web && pnpm dev

test: test-contracts test-py

test-contracts:
	source scripts/env.sh
	cd contracts && forge test

test-py:
	source scripts/env.sh
	$$PY -m pytest -q model/tests attestor/tests

coverage:
	source scripts/env.sh
	cd contracts && forge coverage --report summary --no-match-test "test_Gas_" --no-match-coverage "(^|/)(test|script)/"

train:
	source scripts/env.sh
	$$PY -m model.train

# Base Sepolia: needs RPC_URL and a funded PRIVATE_KEY in the environment.
deploy-testnet:
	source scripts/env.sh
	: "$${RPC_URL:?set RPC_URL}" "$${PRIVATE_KEY:?set PRIVATE_KEY}"
	cd contracts && MODEL_VERSION_HASH=$$(tr -d ' \r\n' < ../model/artefacts/model_version.txt) \
	  forge script script/Deploy.s.sol:Deploy --rpc-url "$$RPC_URL" --private-key "$$PRIVATE_KEY" --broadcast --verify
