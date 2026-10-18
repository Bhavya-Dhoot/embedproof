# Deploying EmbedProof (Base Sepolia, Fly, Vercel) and submitting

Everything local is done and tested (`make demo`). These steps need accounts and testnet funds that
only the team holds. Each step says how to check it worked.

## 0. What you need

| Item | Where | Notes |
| --- | --- | --- |
| Deployer key with Base Sepolia ETH (~0.05) | any wallet + a Base Sepolia faucet | becomes ADMIN and timelock proposer/executor |
| Attestor key (fresh EOA, ~0.02 ETH) | new wallet | signs `attest()`; never the deployer |
| Verifier and regulator addresses | separate wallets | role holders; no funds needed until they act |
| Exporter and two importer wallets | separate wallets, a little ETH each | for the live demo on testnet |
| `ETHERSCAN_API_KEY` (optional) | etherscan.io (v2 key works for Base) | source verification on basescan |
| Pinata JWT (optional) | pinata.cloud | otherwise bundles stay in the attestor's local store |
| Fly.io and Vercel accounts | fly.io, vercel.com | attestor and web hosting |

Never reuse the public Anvil keys anywhere but a local chain. The deploy script refuses to run on a
non-local chain without real `ATTESTOR`, `VERIFIER` and `REGULATOR` addresses.

## 1. Contracts on Base Sepolia

```bash
export RPC_URL=https://sepolia.base.org
export PRIVATE_KEY=<deployer key>
export ATTESTOR=<attestor address> VERIFIER=<verifier address> REGULATOR=<regulator address>
export ETHERSCAN_API_KEY=<optional>
make deploy-testnet
```

Check: `deployments/84532.json` exists; `cast call <registry> "hasRole(bytes32,address)(bool)" $(cast keccak ATTESTOR_ROLE) $ATTESTOR --rpc-url $RPC_URL` returns `true`.
Then register the installations you want to demo (admin):

```bash
cast send <registry> "registerInstallation(bytes32,bytes2,uint8,uint8,uint64,address)" \
  $(cast format-bytes32-string TR-EAF-0001) 0x5452 0 1 300000 <exporter address> \
  --private-key $PRIVATE_KEY --rpc-url $RPC_URL
```

Commit `deployments/84532.json` and paste the four addresses into the README table.

## 2. Attestor on Fly.io

From the repository root:

```bash
fly launch --no-deploy --copy-config --config attestor/fly.toml   # keep app name or pick your own
fly volumes create attestor_data --size 1 --region fra --config attestor/fly.toml
fly secrets set ATTESTOR_PRIVATE_KEY=<attestor key> PINATA_JWT=<optional> --config attestor/fly.toml
fly deploy --config attestor/fly.toml
fly ssh console --config attestor/fly.toml -C "python -m attestor.keys"   # prints the ML-DSA key hash
```

Anchor that hash (the attestor refuses to sign until it matches):

```bash
cast send <registry> "setAttestorPqKey(address,bytes32)" $ATTESTOR <hash> --private-key $PRIVATE_KEY --rpc-url $RPC_URL
```

Check: `curl https://<app>.fly.dev/health` shows `"chainId":84532` and `"pqKeyAnchored":true`.
Set `CORS_ORIGINS` in `attestor/fly.toml` to your Vercel URL before deploying.

## 3. Web on Vercel

Import the GitHub repo, set **Root Directory** to `web`, and add these environment variables:

```text
NEXT_PUBLIC_CHAIN_ID=84532
NEXT_PUBLIC_RPC_URL=https://sepolia.base.org
NEXT_PUBLIC_ATTESTOR_URL=https://<app>.fly.dev
NEXT_PUBLIC_DEMO_MODE=0
NEXT_PUBLIC_IPFS_GATEWAY=https://gateway.pinata.cloud   # only if PINATA_JWT is set
```

The build runs `scripts/sync-deployments.mjs`, which copies `deployments/84532.json` from the repo.
Check: the home page shows "Base Sepolia" and the contract addresses; `/verify/1` loads with no wallet.

## 4. The testnet demo and the `DoubleClaimBlocked` link

Run the four flows in the hosted UI with MetaMask (exporter, verifier, importers). Wallets refuse to
send a transaction that will revert, so record the double-claim revert with `cast`, forcing a gas limit:

```bash
cast send <allocator> "allocate(uint256,bytes32,address,uint32,uint64)" <batchId> \
  $(cast format-bytes32-string CONS-2026-B-001) <importer B> 72083900 5000 \
  --gas-limit 200000 --private-key <exporter key> --rpc-url $RPC_URL
```

The receipt has `status 0`; open it on `https://sepolia.basescan.org/tx/<hash>` and use that link in
Devpost (basescan decodes the `DoubleClaimBlocked` error when the source is verified).

## 5. Devpost checklist

Use `docs/DEVPOST.md` as the description (first line is the track). Fill every **[BD]** item: the
€ comparison (only after confirming the default-value figure), judges' citations, links (repo, hosted
demo, basescan `DoubleClaimBlocked` transaction, video). Upload the narrated video unlisted and test
the link in a private window. Confirm student eligibility and submit by 24 Oct 2026.
