"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { createWalletClient, http, type Abi, type Address, type Hex, type TransactionReceipt, type WalletClient } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { useAccount, useConnect, useDisconnect, useWalletClient } from "wagmi";
import { chain, publicClient } from "./client";
import { CHAIN_ID, DEMO_MODE, RPC_URL } from "./env";
import { withExtraErrors } from "./deployments";
import type { Persona, PersonaKey } from "./demoAccounts";

export type PublicPersona = Omit<Persona, "privateKey">;

export interface TxOutcome {
  hash: Hex;
  receipt: TransactionReceipt;
}

export interface WriteParams {
  address: Address;
  abi: Abi;
  functionName: string;
  args?: readonly unknown[];
}

export interface SignerState {
  mode: "demo" | "wallet";
  /** false until personas (demo) or wallet auto-reconnect (wallet) have settled */
  ready: boolean;
  address?: Address;
  personaKey?: PersonaKey;
  personas: PublicPersona[];
  selectPersona: (key: PersonaKey) => void;
  connect: () => void;
  disconnect: () => void;
  /** EIP-191 personal_sign over 32 raw bytes (what the attestor and contracts expect) */
  signRaw: (hash: Hex) => Promise<Hex>;
  /** EIP-191 personal_sign over a UTF-8 text */
  signText: (text: string) => Promise<Hex>;
  /** simulate -> send -> wait for receipt; throws viem errors (decode with decodeError) */
  write: (p: WriteParams) => Promise<TxOutcome>;
}

const Ctx = createContext<SignerState | null>(null);
const STORE_KEY = "embedproof.persona";

export function SignerProvider({ children }: { children: ReactNode }) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [personaKey, setPersonaKey] = useState<PersonaKey | undefined>(undefined);
  const [demoReady, setDemoReady] = useState(!DEMO_MODE);

  // Demo keys live in their own chunk; this branch is dead code (and the chunk absent) when demo mode is off.
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_DEMO_MODE === "1") {
      if (!DEMO_MODE) return; // demo flag on but not the local chain: never load the keys
      import("./demoAccounts").then((m) => {
        setPersonas(m.DEMO_PERSONAS);
        try {
          const saved = sessionStorage.getItem(STORE_KEY) as PersonaKey | null;
          if (saved && m.DEMO_PERSONAS.some((p) => p.key === saved)) setPersonaKey(saved);
        } catch {
          /* storage unavailable: fine */
        }
        setDemoReady(true);
      });
    }
  }, []);

  const selectPersona = useCallback((key: PersonaKey) => {
    setPersonaKey(key);
    try {
      sessionStorage.setItem(STORE_KEY, key);
    } catch {
      /* ignore */
    }
  }, []);

  const persona = useMemo(() => personas.find((p) => p.key === personaKey), [personas, personaKey]);
  const demoWallet = useMemo(
    () => (persona ? createWalletClient({ account: privateKeyToAccount(persona.privateKey), chain, transport: http(RPC_URL) }) : undefined),
    [persona],
  );

  // injected wallet (wagmi)
  const acct = useAccount();
  const { connect: wagmiConnect, connectors } = useConnect();
  const { disconnect: wagmiDisconnect } = useDisconnect();
  const { data: wagmiWallet } = useWalletClient();

  const mode: "demo" | "wallet" = DEMO_MODE ? "demo" : "wallet";
  const walletClient: WalletClient | undefined = mode === "demo" ? demoWallet : (wagmiWallet as WalletClient | undefined);
  const address: Address | undefined = mode === "demo" ? persona?.address : acct.address;

  const ensureChain = useCallback(async (wc: WalletClient) => {
    if (mode !== "wallet") return;
    const current = await wc.getChainId();
    if (current !== CHAIN_ID) {
      try {
        await wc.switchChain({ id: CHAIN_ID });
      } catch {
        await wc.addChain({ chain });
        await wc.switchChain({ id: CHAIN_ID });
      }
    }
  }, [mode]);

  const signRaw = useCallback(
    async (hash: Hex) => {
      if (!walletClient?.account) throw new Error("No signer selected");
      await ensureChain(walletClient);
      return walletClient.signMessage({ account: walletClient.account, message: { raw: hash } });
    },
    [walletClient, ensureChain],
  );

  const signText = useCallback(
    async (text: string) => {
      if (!walletClient?.account) throw new Error("No signer selected");
      await ensureChain(walletClient);
      return walletClient.signMessage({ account: walletClient.account, message: text });
    },
    [walletClient, ensureChain],
  );

  const write = useCallback(
    async (p: WriteParams): Promise<TxOutcome> => {
      if (!walletClient?.account) throw new Error("No signer selected");
      await ensureChain(walletClient);
      const abi = withExtraErrors(p.abi);
      // simulate first so reverts surface as decoded custom errors (name + args), for demo and injected wallets alike
      const { request } = await publicClient.simulateContract({
        account: walletClient.account,
        address: p.address,
        abi,
        functionName: p.functionName,
        args: p.args,
      } as never);
      const hash = await walletClient.writeContract(request as never);
      const receipt = await publicClient.waitForTransactionReceipt({ hash, pollingInterval: CHAIN_ID === 31337 ? 300 : 2000 });
      if (receipt.status !== "success") throw new Error("Transaction was mined but reverted.");
      return { hash, receipt };
    },
    [walletClient, ensureChain],
  );

  const value: SignerState = {
    mode,
    ready: mode === "demo" ? demoReady : acct.status !== "reconnecting" && acct.status !== "connecting",
    address,
    personaKey,
    personas: personas.map((p) => ({ key: p.key, anvilIndex: p.anvilIndex, label: p.label, role: p.role, address: p.address })),
    selectPersona,
    connect: () => {
      const c = connectors[0];
      if (c) wagmiConnect({ connector: c });
    },
    disconnect: () => (mode === "demo" ? setPersonaKey(undefined) : wagmiDisconnect()),
    signRaw,
    signText,
    write,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSigner(): SignerState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSigner must be used inside <SignerProvider>");
  return v;
}
