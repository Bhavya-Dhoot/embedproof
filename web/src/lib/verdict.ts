import type { StatusName } from "./report";
import type { Check, CheckId, VerifyReport } from "./verify";

export interface VerdictInput {
  /** result of verifyBundle (or the edited copy); null while it runs */
  report: VerifyReport | null;
  /** extra checks done outside verifyBundle (the reason text hash) */
  extraChecks?: Check[];
  bundleError?: string;
  status: StatusName;
  revoked: boolean;
  /** true while the user is looking at an edited copy of the document */
  edited?: boolean;
  allocatedTonnes?: bigint;
  attestedTonnes: bigint;
}

export interface Verdict {
  tone: "green" | "amber" | "red" | "neutral";
  title: string;
  text: string;
}

/** One plain sentence per kind of failure, most fundamental first. */
const FAILURE_PHRASE: [CheckId[], (edited: boolean) => string][] = [
  [["cid", "bundleCommitment"], () => "the stored bundle does not match the on-chain record"],
  [["ecdsaOnchain"], () => "the attestor's ECDSA signature does not match this batch's on-chain data"],
  [["pqKeyAnchored"], () => "the post-quantum key in the bundle is not one anchored on-chain for this attestor"],
  [["pqHash"], () => "the post-quantum signature does not match its on-chain hash"],
  [["decrypt"], () => "the report could not be decrypted with this key"],
  [["commitment", "ecdsaDoc", "mldsa"], (edited) => (edited ? "this edited copy no longer matches what the attestor signed" : "the report no longer matches what the attestor signed")],
  [["fieldsMatch"], () => "the report's numbers differ from the on-chain record"],
  [["reasonHash"], () => "the reason text does not match its on-chain hash"],
];

export function failureSentence(checks: Check[], edited = false): string {
  const failed = new Set(checks.filter((c) => c.status === "fail").map((c) => c.id));
  const phrases = FAILURE_PHRASE.filter(([ids]) => ids.some((id) => failed.has(id))).map(([, f]) => f(edited));
  return phrases.length ? phrases.slice(0, 2).join("; and ") : "an unexpected check failed";
}

const t = (n: bigint) => Number(n).toLocaleString("en-US");

/** The plain-language banner at the top of the certificate page. */
export function verdictFor(i: VerdictInput): Verdict {
  const alloc = i.allocatedTonnes === undefined ? "" : `, ${t(i.allocatedTonnes)} of ${t(i.attestedTonnes)} t allocated`;
  if (i.bundleError) {
    return { tone: "amber", title: "Cannot verify yet", text: "the signed bundle could not be fetched, so no signature was checked. The on-chain data below is still real." };
  }
  if (!i.report) return { tone: "neutral", title: "Checking", text: "reading the chain and verifying the signatures in your browser." };

  const checks = [...i.report.checks, ...(i.extraChecks ?? [])];
  if (checks.some((c) => c.status === "fail")) {
    return { tone: "red", title: "Verification failed", text: `${failureSentence(checks, i.edited)}.` };
  }

  const signed = "signed by the EmbedProof attestor (ECDSA + ML-DSA)";
  const state =
    i.revoked || i.status === "Rejected"
      ? { tone: "amber" as const, title: "Signatures valid, batch rejected", tail: ". The contract no longer allows these tonnes to be allocated" }
      : i.status === "PendingVerifier"
        ? { tone: "amber" as const, title: "Signatures valid, waiting for a verifier", tail: ". A human verifier has to decide before any tonne can be allocated" }
        : { tone: "green" as const, title: "Valid certificate", tail: "" };

  if (i.report.sealed) {
    return {
      tone: state.tone,
      title: state.tone === "green" ? "Signatures valid" : state.title,
      text: `${signed}${alloc}${state.tail}. The report itself is sealed: open the certificate link to check it.`,
    };
  }
  if (i.edited) return { tone: "green", title: "Edited copy passes", text: "this edited copy still matches what the attestor signed (only whitespace or formatting changed)." };
  return { tone: state.tone, title: state.title, text: `${signed}, report unchanged since signing${alloc}${state.tail}.` };
}
