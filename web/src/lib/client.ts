import { createPublicClient, http, type Chain } from "viem";
import { baseSepolia, foundry } from "viem/chains";
import { createConfig, injected } from "wagmi";
import { CHAIN_ID, RPC_URL } from "./env";

const base = CHAIN_ID === 84532 ? baseSepolia : foundry;

export const chain: Chain = {
  ...base,
  rpcUrls: { default: { http: [RPC_URL] } },
};

/** One shared read client for the whole app (no wallet needed; used by the public /verify page too). */
export const publicClient = createPublicClient({
  chain,
  transport: http(RPC_URL),
  pollingInterval: CHAIN_ID === 31337 ? 400 : 2000,
});

export type AppPublicClient = typeof publicClient;

export const wagmiConfig = createConfig({
  chains: [chain],
  connectors: [injected()],
  transports: { [chain.id]: http(RPC_URL) },
  ssr: true,
});
