// NEXT_PUBLIC_* must be referenced literally so Next can inline them at build time.
const chainIdRaw = Number(process.env.NEXT_PUBLIC_CHAIN_ID ?? "31337");
export const CHAIN_ID: 31337 | 84532 = chainIdRaw === 84532 ? 84532 : 31337;

export const RPC_URL: string =
  process.env.NEXT_PUBLIC_RPC_URL || (CHAIN_ID === 84532 ? "https://sepolia.base.org" : "http://127.0.0.1:8545");

export const ATTESTOR_URL: string = (process.env.NEXT_PUBLIC_ATTESTOR_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

/** Persona switcher with PUBLIC Anvil test keys. Only ever on for the local chain. */
export const DEMO_MODE: boolean = process.env.NEXT_PUBLIC_DEMO_MODE === "1" && CHAIN_ID === 31337;

export const IPFS_GATEWAY: string = process.env.NEXT_PUBLIC_IPFS_GATEWAY || "";

export const CHAIN_LABEL = CHAIN_ID === 84532 ? "Base Sepolia" : "Local Anvil";

/** FR-F6: explorer link for a tx; null on the local chain (show the hash + "local Anvil"). */
export function explorerTxUrl(hash: string): string | null {
  return CHAIN_ID === 84532 ? `https://sepolia.basescan.org/tx/${hash}` : null;
}

export function explorerAddressUrl(addr: string): string | null {
  return CHAIN_ID === 84532 ? `https://sepolia.basescan.org/address/${addr}` : null;
}

/** Where to fetch a bundle: the configured IPFS gateway, else the attestor's local fallback. */
export function bundleUrl(cid: string): string {
  if (IPFS_GATEWAY) {
    const gw = IPFS_GATEWAY.replace(/\/+$/, "");
    const hasPath = /^https?:\/\/[^/]+\/.+/.test(gw);
    return `${hasPath ? gw : `${gw}/ipfs`}/${cid}`;
  }
  return `${ATTESTOR_URL}/bundle/${cid}`;
}
