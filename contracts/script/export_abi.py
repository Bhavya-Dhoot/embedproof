#!/usr/bin/env python3
"""Export contract ABIs for the web and attestor builds (stdlib only).

    python contracts/script/export_abi.py                        # after `forge build`
    python contracts/script/export_abi.py --fix-deploy-block 31337   # after `forge script ... --broadcast`

Default: copies the `abi` array of contracts/out/<Name>.sol/<Name>.json to deployments/abi/<Name>.json.

--fix-deploy-block <chainId>: Deploy.s.sol records the chain head at script start as `deployBlock`
(a safe lower bound). This replaces it with the exact block of the first deployment transaction, read
from the forge broadcast receipts (contracts/broadcast/Deploy.s.sol/<chainId>/run-latest.json).
"""
import json
import sys
from pathlib import Path

NAMES = ["InstallationRegistry", "EmissionsAttestation", "ConsignmentAllocator"]
CONTRACTS = Path(__file__).resolve().parents[1]  # contracts/
DEPLOYMENTS = CONTRACTS.parent / "deployments"


def export_abis() -> None:
    out_dir = DEPLOYMENTS / "abi"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        artifact = CONTRACTS / "out" / f"{name}.sol" / f"{name}.json"
        if not artifact.exists():
            sys.exit(f"missing {artifact} - run `forge build` first")
        abi = json.loads(artifact.read_text(encoding="utf-8"))["abi"]
        target = out_dir / f"{name}.json"
        target.write_text(json.dumps(abi, indent=2) + "\n", encoding="utf-8")
        print(f"{target.relative_to(CONTRACTS.parent)}  ({len(abi)} entries)")


def fix_deploy_block(chain_id: str) -> None:
    run = CONTRACTS / "broadcast" / "Deploy.s.sol" / chain_id / "run-latest.json"
    dep = DEPLOYMENTS / f"{chain_id}.json"
    if not run.exists():
        sys.exit(f"missing {run} - run the deploy with --broadcast first")
    if not dep.exists():
        sys.exit(f"missing {dep}")
    receipts = json.loads(run.read_text(encoding="utf-8")).get("receipts", [])
    if not receipts:
        sys.exit(f"no receipts in {run} (was the broadcast confirmed?)")
    block = min(int(r["blockNumber"], 16) for r in receipts)
    data = json.loads(dep.read_text(encoding="utf-8"))
    data["deployBlock"] = block
    dep.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"{dep.relative_to(CONTRACTS.parent)}: deployBlock = {block}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--fix-deploy-block"] and len(args) == 2:
        fix_deploy_block(args[1])
    elif not args:
        export_abis()
    else:
        sys.exit(__doc__)
