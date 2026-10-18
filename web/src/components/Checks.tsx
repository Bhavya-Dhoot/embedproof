"use client";

import type { ReactNode } from "react";
import type { Check, CheckId, CheckStatus, VerifyReport } from "@/lib/verify";
import { cx } from "./ui";

const COPY: Record<CheckId, { title: string; plain: string }> = {
  cid: { title: "Stored file is the one the chain points to", plain: "SHA-256 of the downloaded bundle equals the digest recorded on-chain (its IPFS content id)." },
  bundleCommitment: { title: "Bundle fingerprint equals the on-chain fingerprint", plain: "The commitment inside the bundle is the same 32 bytes the contract stored." },
  ecdsaOnchain: { title: "ECDSA signature matches the attestor on-chain", plain: "Recovering the signer from the signature over the on-chain commitment gives the attestor address the contract recorded." },
  ecdsaDoc: { title: "ECDSA signature is valid over this exact report", plain: "Re-hash the report, recover the signer, compare with the on-chain attestor." },
  pqHash: { title: "Post-quantum signature fingerprint matches the chain", plain: "keccak-256 of the ML-DSA-44 signature equals pqSigHash stored by the contract." },
  pqKeyAnchored: { title: "ML-DSA key is anchored on-chain", plain: "keccak-256 of the public key inside the bundle equals the post-quantum key hash the registry holds for this attestor, so a stranger cannot sign with a key of their own." },
  mldsa: { title: "ML-DSA-44 signature is valid over this exact report", plain: "Verified in your browser with the NIST FIPS 204 algorithm; no server involved." },
  decrypt: { title: "Report unsealed with the certificate key", plain: "AES-256-GCM, authenticated against the on-chain commitment. A wrong key or any edit to the ciphertext fails here." },
  commitment: { title: "Report fingerprint equals the on-chain commitment", plain: "keccak-256 of the canonical (RFC 8785) report recomputed in your browser." },
  fieldsMatch: { title: "Report fields match the chain", plain: "Installation, period, SEE, tonnes, risk score, model version, chain and contract inside the signed report equal what the contract stored. The signature only covers the fingerprint, so this ties the on-chain numbers to the signed document." },
};

const GROUPS: { title: string; sub: string; ids: CheckId[] }[] = [
  { title: "Stored file", sub: "IPFS bundle", ids: ["cid", "bundleCommitment"] },
  { title: "Signature 1 · ECDSA secp256k1", sub: "classical, checkable on-chain", ids: ["ecdsaOnchain", "ecdsaDoc"] },
  { title: "Signature 2 · ML-DSA-44", sub: "post-quantum, FIPS 204", ids: ["pqKeyAnchored", "pqHash", "mldsa"] },
  { title: "Report integrity", sub: "encrypted off-chain, fingerprint on-chain", ids: ["decrypt", "commitment", "fieldsMatch"] },
];

function Icon({ s }: { s: CheckStatus }) {
  const base = "grid h-7 w-7 shrink-0 place-items-center rounded-full text-sm font-bold";
  if (s === "pass") return <span className={cx(base, "bg-[var(--color-teal)] text-[var(--color-ink)]")} aria-hidden>✓</span>;
  if (s === "fail") return <span className={cx(base, "bg-[var(--color-rose)] text-[var(--color-ink)]")} aria-hidden>✕</span>;
  if (s === "sealed") return <span className={cx(base, "border border-white/60 bg-white/10")} aria-hidden>🔒</span>;
  return <span className={cx(base, "border border-white/40 bg-white/5")} aria-hidden>-</span>;
}

const WORD: Record<CheckStatus, string> = { pass: "passed", fail: "FAILED", sealed: "sealed: open with the certificate link", skipped: "skipped" };

function Row({ c }: { c: Check }) {
  const copy = COPY[c.id];
  return (
    <li className={cx("flex gap-3 rounded-xl p-2.5", c.status === "fail" && "bg-[var(--color-rose)]/15")}>
      <Icon s={c.status} />
      <div className="min-w-0 flex-1">
        <p className="text-[0.95rem] font-bold leading-snug">
          {copy.title} <span className="sr-only">: {WORD[c.status]}</span>
        </p>
        <p className="text-sm text-[var(--color-mute)]">{c.status === "sealed" ? "Sealed: open this page from the certificate link (QR) to check it." : copy.plain}</p>
        <details className="mt-0.5">
          <summary className="cursor-pointer text-xs text-white/75 hover:text-white">technical detail</summary>
          <p className="mono mt-1 text-white/85">{c.detail}</p>
        </details>
      </div>
    </li>
  );
}

/** Big headline for one signature: ECDSA or ML-DSA. */
export function SigBadge({ label, status, note }: { label: string; status: CheckStatus | "pending"; note?: string }) {
  const tone =
    status === "pass"
      ? "border-[var(--color-teal)] text-[var(--color-teal-text)]"
      : status === "fail"
        ? "border-[var(--color-rose)] text-[var(--color-rose-text)]"
        : "border-white/40 text-white/85";
  const mark = status === "pass" ? "✓" : status === "fail" ? "✕" : status === "sealed" ? "🔒" : "…";
  const word = status === "pass" ? "valid" : status === "fail" ? "FAILED" : status === "sealed" ? "sealed" : "checking";
  return (
    <div className={cx("tile flex flex-1 items-center gap-3 border-2 px-4 py-3", tone)} role="status">
      <span className="num text-3xl" aria-hidden>{mark}</span>
      <div>
        <p className="text-sm font-bold text-white">{label}</p>
        <p className="num text-lg">{word}</p>
        {note && <p className="text-xs text-white/75">{note}</p>}
      </div>
    </div>
  );
}

function combine(...ss: (CheckStatus | undefined)[]): CheckStatus | "pending" {
  const l = ss.filter(Boolean) as CheckStatus[];
  if (l.length === 0) return "pending";
  if (l.includes("fail")) return "fail";
  if (l.includes("sealed")) return "sealed";
  if (l.includes("skipped")) return "skipped";
  return "pass";
}

export function signatureStatuses(r: VerifyReport | null) {
  const st = (id: CheckId) => r?.checks.find((c) => c.id === id)?.status;
  // ECDSA is public: without the key it is still fully checked against the on-chain commitment,
  // so a sealed document-level row must not downgrade the headline. ML-DSA needs the document, so it stays sealed.
  const ecdsaDoc = st("ecdsaDoc");
  return {
    ecdsa: combine(st("ecdsaOnchain"), ecdsaDoc === "sealed" ? undefined : ecdsaDoc),
    mldsa: combine(st("pqKeyAnchored"), st("pqHash"), st("mldsa")),
  };
}

export function CheckGroups({ report, footer }: { report: VerifyReport | null; footer?: ReactNode }) {
  if (!report) return <p className="text-sm text-[var(--color-mute)]">Running checks…</p>;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {GROUPS.map((g) => {
        const rows = g.ids.map((id) => report.checks.find((c) => c.id === id)).filter(Boolean) as Check[];
        return (
          <div key={g.title} className="tile p-3 sm:p-4">
            <div className="mb-2 px-1">
              <h3 className="text-base font-bold">{g.title}</h3>
              <p className="text-xs text-white/75">{g.sub}</p>
            </div>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {rows.map((c) => (
                <Row key={c.id} c={c} />
              ))}
            </ul>
          </div>
        );
      })}
      {footer}
    </div>
  );
}
