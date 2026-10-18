import type { Hex } from "viem";
import { ATTESTOR_URL, bundleUrl } from "./env";
import { plain, type AttestResult, type Report, type ScoreResult } from "./report";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    /** decoded contract revert when the attestor's own tx failed (HTTP 409) */
    public revert?: { name: string; args: unknown[] },
    /** seconds from a Retry-After header (HTTP 429) */
    public retryAfter?: number,
  ) {
    super(detail);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${ATTESTOR_URL}${path}`, { ...init, headers: { "content-type": "application/json", ...(init?.headers ?? {}) } });
  } catch {
    throw new ApiError(0, `Attestor unreachable at ${ATTESTOR_URL}. Is it running (make demo)?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    let revert: ApiError["revert"];
    try {
      const j = await res.json();
      detail = typeof j?.detail === "string" ? j.detail : JSON.stringify(j?.detail ?? j);
      if (j?.revert?.name) revert = { name: String(j.revert.name), args: Array.isArray(j.revert.args) ? j.revert.args : [] };
    } catch {
      /* keep statusText */
    }
    const ra = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, plain(detail), revert, Number.isFinite(ra) && ra > 0 ? ra : undefined);
  }
  return (await res.json()) as T;
}

export interface Health {
  ok: boolean;
  chainId: number;
  attestor: string;
  modelVersionHash: Hex;
  pqBackend: string;
  ipfsMode: string;
  /** keccak256 of the raw ML-DSA-44 public key the attestor signs with */
  pqPublicKeyHash?: Hex;
}

export interface DemoStatus {
  enabled: boolean;
  chainId?: number;
  chainName?: string;
}

export interface DemoStep {
  label: string;
  ok: boolean;
  /** true when the outcome is what the scenario intends: ok=false with expected=true is a deliberate rejection, not an error */
  expected: boolean;
  txHash: Hex | null;
  detail: string;
}

export interface DemoRunResult {
  scenario: string;
  chainId: number;
  batchId: number | string | null;
  /** /verify/<id>#k=<key> */
  certificatePath: string | null;
  steps: DemoStep[];
}

export const DEMO_SCENARIOS = ["green", "red", "double-claim"] as const;
export type DemoScenario = (typeof DEMO_SCENARIOS)[number];

export const attestorApi = {
  demoStatus: () => call<DemoStatus>("/demo/status"),
  demoRun: (scenario: DemoScenario) => call<DemoRunResult>(`/demo/run/${scenario}`, { method: "POST" }),
  health: () => call<Health>("/health"),
  score: (report: Report) => call<ScoreResult>("/score", { method: "POST", body: JSON.stringify(report) }),
  attest: (report: Report, signature: Hex) => call<AttestResult>("/attest", { method: "POST", body: JSON.stringify({ report, signature }) }),
  disclosure: (batchId: bigint | number | string, address: string, message: string, signature: Hex) =>
    call<{ keyB64url: string; document: Record<string, unknown> }>(`/disclosure/${batchId}`, {
      method: "POST",
      body: JSON.stringify({ address, message, signature }),
    }),
  postReason: (batchId: bigint | number | string, text: string) =>
    call<{ reasonHash: Hex }>("/reasons", { method: "POST", body: JSON.stringify({ batchId: Number(batchId), text }) }),
  getReason: (hash: string) => call<{ text: string }>(`/reasons/${hash}`),
};

/** Fetch the raw bundle bytes (IPFS gateway or the attestor fallback). Throws a readable error. */
export async function fetchBundleBytes(cid: string, signal?: AbortSignal): Promise<Uint8Array> {
  const url = bundleUrl(cid);
  let res: Response;
  try {
    res = await fetch(url, { signal, cache: "no-store" });
  } catch {
    throw new Error(`Could not reach the bundle store (${url}).`);
  }
  if (!res.ok) throw new Error(`Bundle not found (${res.status}) at ${url}`);
  return new Uint8Array(await res.arrayBuffer());
}

export const disclosureMessage = (batchId: bigint | number | string) =>
  `EmbedProof disclosure\nbatch:${batchId}\nts:${Math.floor(Date.now() / 1000)}`;
