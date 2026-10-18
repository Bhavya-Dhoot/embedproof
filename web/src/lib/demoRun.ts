import { ApiError, type DemoStep } from "./attestor";

/** Sepolia explorer link for a step's tx; the local chain has no explorer, so only the hash is shown. */
export function explorerTx(chainId: number, hash: string): string | null {
  return chainId === 84532 ? `https://sepolia.basescan.org/tx/${hash}` : null;
}

/** One step's outcome in words: ok=false with expected=true is a deliberate rejection, not an error. */
export function stepOutcome(s: Pick<DemoStep, "ok" | "expected">): { tone: "green" | "amber" | "red"; text: string } {
  if (!s.expected) return { tone: "red", text: "Unexpected result" };
  return s.ok ? { tone: "green", text: "Done" } : { tone: "amber", text: "Rejected as expected" };
}

export function describeRunError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return "That scenario is not available on this attestor.";
    if (e.status === 409) return "A scenario is already running on this chain. Try again in a moment.";
    if (e.status === 429) return `Too many runs from this address. Try again${e.retryAfter ? ` in ${e.retryAfter} seconds` : " in a minute"}.`;
    return e.detail;
  }
  return e instanceof Error ? e.message : "The run failed.";
}
