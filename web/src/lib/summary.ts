import type { ScoreResult } from "./report";

type SummaryScore = Pick<ScoreResult, "band" | "expectedLow" | "expectedHigh" | "cbamDefault">;

/** "In plain words": at most 25 words, built from the numbers, never from the model's own long sentence. */
export function plainSummary(s: SummaryScore, see: number): string {
  const pos = see < s.expectedLow ? "below" : see > s.expectedHigh ? "above" : "inside";
  const head = `Declared ${see.toFixed(3)} tCO₂/t is ${pos} the ${s.expectedLow.toFixed(2)} to ${s.expectedHigh.toFixed(2)} range expected for this plant.`;
  const tail = s.band === "red" ? " A human verifier must review it." : s.band === "amber" ? " It is attested, with a visible flag." : "";
  return head + tail;
}

/** The EC default comparison as a plain fact (not a risk): "77% below the EC default of 2.31 for this route". */
export function defaultComparison(see: number, cbamDefault: number): string | null {
  if (!(cbamDefault > 0)) return null;
  const pct = (1 - see / cbamDefault) * 100;
  return `${Math.abs(pct).toFixed(0)}% ${pct >= 0 ? "below" : "above"} the EC default value of ${cbamDefault.toFixed(2)} for this route (the default counts direct emissions only; the declared SEE includes indirect).`;
}

const FLAG_FALLBACK: Record<string, string> = {
  GRID_FACTOR_BELOW_EMBER: "The declared grid factor is below the Ember figure for this country.",
  ELECTRICITY_OUTSIDE_ROUTE_NORM: "Electricity use per tonne is outside the usual range for this route.",
};

/** Text for a rule flag: the attestor's message when present, else a built-in wording for the known codes, else the code. */
export function flagText(f: { code: string; message?: string } | string): string {
  const code = typeof f === "string" ? f : f.code;
  const message = typeof f === "string" ? undefined : f.message;
  return message || FLAG_FALLBACK[code] || code;
}

export const NO_HISTORY_NOTE = "No plant history: wider interval, lower detection power";

export const wordCount = (s: string) => s.trim().split(/\s+/).filter(Boolean).length;
