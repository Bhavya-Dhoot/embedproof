"use client";

import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { zeroAddress, type Address, type Hex } from "viem";
import { CheckGroups, SigBadge, signatureStatuses } from "@/components/Checks";
import { PlausibilityView } from "@/components/Plausibility";
import { BandChip, Chip, ConsignmentId, Glass, Mono, NeedsDeployment, Notice, PageHeader, Spinner, StatusChip, Tile, TxLink, cx } from "@/components/ui";
import { ApiError, attestorApi, fetchBundleBytes } from "@/lib/attestor";
import { readBatch, readBatchRevocation, readIsAttestorPqKey, type AllocationView, type BatchView } from "@/lib/chainReads";
import { publicClient } from "@/lib/client";
import { cidFromDigest } from "@/lib/cid";
import { INSTALLATION_NAMES, labelAddress } from "@/lib/demoMeta";
import { diffText } from "@/lib/diff";
import { CHAIN_ID, bundleUrl } from "@/lib/env";
import { useAllocationsForBatch, useDeployment } from "@/lib/hooks";
import { bandFor, formatSee, formatTonnes, plain, shortAddr, uint32ToPeriod, type ScoreResult } from "@/lib/report";
import {
  MLDSA44_PK_BYTES,
  MLDSA44_SIG_BYTES,
  bytesToB64,
  parseKeyFromHash,
  tamperOneDigit,
  verifyBundle,
  type OnchainBatch,
  type VerifyReport,
} from "@/lib/verify";

const ZERO32 = ("0x" + "00".repeat(32)) as Hex;

interface Cert {
  batch: BatchView;
  cid: string;
  bundleBytes?: Uint8Array;
  bundleError?: string;
}

function useCertificate(batchId: bigint | null): { data?: Cert; isLoading: boolean; isError: boolean } {
  const dep = useDeployment();
  // chain state polls; the bundle is content-addressed (its sha-256 is the on-chain digest), so it is fetched once per CID
  const chainQ = useQuery({
    queryKey: ["chain", "certificate", dep?.attestation, batchId?.toString()],
    enabled: !!dep && batchId !== null,
    refetchInterval: 6000,
    retry: false,
    queryFn: () => readBatch(publicClient, dep!, batchId!),
  });
  const cid = chainQ.data ? cidFromDigest(chainQ.data.cidDigest) : undefined;
  const bundleQ = useQuery({
    queryKey: ["bundle", cid],
    enabled: !!cid,
    staleTime: Infinity,
    retry: 1,
    queryFn: () => fetchBundleBytes(cid!),
  });
  const data: Cert | undefined = chainQ.data && cid
    ? {
        batch: chainQ.data,
        cid,
        bundleBytes: bundleQ.data,
        bundleError: bundleQ.isError ? (bundleQ.error as Error).message : undefined,
      }
    : undefined;
  return { data, isLoading: chainQ.isLoading, isError: chainQ.isError };
}

/** The key lives in the URL fragment. Re-read it on hashchange AND when the batch in the path changes (client-side navigation keeps this component mounted). */
function useFragmentKey(batchSegment: string) {
  const [key, setKey] = useState<Uint8Array | null>(null);
  useEffect(() => {
    const read = () => setKey(parseKeyFromHash(window.location.hash));
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, [batchSegment]);
  return [key, setKey] as const;
}

function asScore(doc: unknown): ScoreResult | null {
  const a = (doc as { assessment?: ScoreResult } | null)?.assessment;
  return a && typeof a.riskScore === "number" && Array.isArray(a.shap) ? a : null;
}

function QrCode() {
  const [src, setSrc] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  useEffect(() => {
    const make = () => {
      const href = window.location.href;
      setUrl(href);
      import("qrcode").then((q) => q.toDataURL(href, { margin: 1, width: 240, color: { dark: "#0B1020", light: "#FFFFFF" }, errorCorrectionLevel: "M" })).then(setSrc, () => setSrc(null));
    };
    make();
    window.addEventListener("hashchange", make);
    return () => window.removeEventListener("hashchange", make);
  }, []);
  return (
    <div className="flex flex-col items-center gap-3 text-center sm:flex-row sm:text-left">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {src ? <img src={src} alt="QR code linking to this certificate" width={160} height={160} className="rounded-2xl bg-white p-2" /> : <div className="h-40 w-40 rounded-2xl bg-white/10" />}
      <div>
        <p className="text-lg font-bold">Scan to verify on any phone</p>
        <p className="text-sm text-[var(--color-mute)]">
          This page needs no wallet and no login. The QR carries the full link{url.includes("#k=") ? ", including the key that unseals the report" : ""}.
        </p>
      </div>
    </div>
  );
}

function ReasonText({ hash }: { hash: Hex }) {
  const q = useQuery({ queryKey: ["reason", hash], queryFn: () => attestorApi.getReason(hash), retry: false, staleTime: Infinity });
  if (q.isLoading) return <Spinner label="loading reason" />;
  if (q.data) return <q className="block text-base italic">{plain(q.data.text)}</q>;
  return <span className="text-sm text-[var(--color-mute)]">Reason text is held by the attestor ({q.error instanceof ApiError ? q.error.detail : "unavailable"}); its hash is recorded on-chain.</span>;
}

function AllocationTable({ rows, verified, accounts }: { rows: AllocationView[]; verified: bigint; accounts?: Record<string, string | undefined> }) {
  const allocated = rows.reduce((s, r) => s + r.tonnes, 0n);
  const remaining = verified > allocated ? verified - allocated : 0n;
  const pct = verified > 0n ? Number((allocated * 10000n) / verified) / 100 : 0;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-bold">
            <span className="num text-2xl">{formatTonnes(allocated)}</span> of <span className="num">{formatTonnes(verified)}</span> verified tonnes allocated
          </p>
          <p className="text-sm text-[var(--color-mute)]">
            <span className="num">{formatTonnes(remaining)}</span> t remaining
          </p>
        </div>
        <div className="h-3 overflow-hidden rounded-full bg-white/15" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Allocated share of verified tonnes">
          <div className="h-full rounded-full" style={{ width: `${pct}%`, background: "linear-gradient(90deg,#00C2A8,#3B2BFF)", transition: "width .9s cubic-bezier(.2,.8,.2,1)" }} />
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-[var(--color-mute)]">No tonnes allocated yet. The exporter assigns them to consignments; the contract refuses to allocate more than {formatTonnes(verified)} t.</p>
      ) : (
        <div className="scroll-x">
          <table className="tbl">
            <thead>
              <tr>
                <th>Holder</th>
                <th>Consignment</th>
                <th>CN code</th>
                <th className="text-right">Tonnes</th>
                <th>Claimed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.consignmentId}>
                  <td>
                    <span className="font-bold">{labelAddress(r.importer, accounts) ?? "Importer"}</span> <span className="mono block text-white/75">{shortAddr(r.importer)}</span>
                  </td>
                  <td><ConsignmentId id={r.consignmentId} /></td>
                  <td className="num">{r.cnCode}</td>
                  <td className="num text-right">{formatTonnes(r.tonnes)}</td>
                  <td>{r.claimed ? <Chip tone="green">claimed</Chip> : <Chip>open</Chip>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TamperPanel({
  original,
  editText,
  setEditText,
  live,
}: {
  original: string;
  editText: string | null;
  setEditText: (s: string | null) => void;
  live: VerifyReport | null;
}) {
  const text = editText ?? original;
  const edited = text !== original;
  const rows = useMemo(() => (edited ? diffText(original, text) : []), [original, text, edited]);
  let parseError: string | null = null;
  if (edited) {
    try {
      JSON.parse(text);
    } catch (e) {
      parseError = (e as Error).message;
    }
  }
  const sig = signatureStatuses(live);
  const commitment = live?.checks.find((c) => c.id === "commitment")?.status;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-danger" onClick={() => setEditText(tamperOneDigit(editText ?? original))}>
          Change one digit
        </button>
        <button className="btn btn-ghost" onClick={() => setEditText(null)} disabled={!edited}>
          Restore original
        </button>
        {edited ? <Chip tone="red">Edited copy · on-chain record untouched</Chip> : <Chip tone="green">Original document</Chip>}
      </div>

      <div className={cx("flex flex-wrap gap-3", edited && live?.anyFailed && "tamper-shake")} key={edited ? "e" : "o"}>
        <SigBadge label="ECDSA secp256k1" status={sig.ecdsa === "skipped" ? "fail" : sig.ecdsa} />
        <SigBadge label="ML-DSA-44 (post-quantum)" status={sig.mldsa === "skipped" ? "fail" : sig.mldsa} />
        <SigBadge label="Fingerprint = on-chain" status={commitment ?? "pending"} />
      </div>

      <div>
        <label className="lbl" htmlFor="doc-json">
          The signed attestation document (editable). Whitespace changes are harmless, values are not.
        </label>
        <textarea
          id="doc-json"
          className="inp mono !min-h-[260px]"
          spellCheck={false}
          value={text}
          onChange={(e) => setEditText(e.target.value)}
          aria-invalid={!!parseError}
        />
        {parseError && <p className="mt-1 text-sm text-[var(--color-rose-text)]">Not valid JSON: {parseError}</p>}
      </div>

      {edited && !parseError && rows.length > 0 && (
        <div className="tile p-3" aria-label="Difference to the original document">
          <p className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-white/75">What changed</p>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {rows.slice(0, 8).map((r, i) => (
              <li key={i} className="mono !text-[0.8rem] leading-relaxed">
                {r.a && (
                  <div className="text-white/80">
                    <span className="mr-2 text-[var(--color-rose-text)]">- {r.aLine}</span>
                    {r.a.map((s, k) => (s.changed ? <mark key={k} className="diff">{s.text}</mark> : <span key={k}>{s.text}</span>))}
                  </div>
                )}
                {r.b && (
                  <div>
                    <span className="mr-2 text-[var(--color-teal-text)]">+ {r.bLine}</span>
                    {r.b.map((s, k) => (s.changed ? <mark key={k} className="diff">{s.text}</mark> : <span key={k}>{s.text}</span>))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Details({ b, cid, accounts, doc }: { b: BatchView; cid: string; accounts?: Record<string, string | undefined>; doc: unknown }) {
  const items: [string, React.ReactNode][] = [
    ["Algorithm id", <span key="a" className="num">{b.algId} <span className="font-normal text-white/80">= ECDSA secp256k1 (EIP-191) + ML-DSA-44</span></span>],
    ["Model version (SHA-256)", <Mono key="m" value={b.modelVersionHash} />],
    ["Attestor", <span key="at" className="inline-flex flex-wrap items-center gap-2"><Mono value={b.attestor} />{labelAddress(b.attestor, accounts) && <Chip>{labelAddress(b.attestor, accounts)}</Chip>}</span>],
    ["Commitment (keccak-256)", <Mono key="c" value={b.commitment} />],
    ["Bundle CID", <Mono key="cid" value={cid} />],
    ["Bundle digest (sha-256)", <Mono key="cd" value={b.cidDigest} />],
    ["ML-DSA signature hash", <Mono key="pq" value={b.pqSigHash} />],
    ["Attested at", <span key="t" className="num">{b.attestedAt ? new Date(b.attestedAt * 1000).toISOString().replace(".000Z", "Z") : "n/a"}</span>],
  ];
  const issued = (doc as { issuedAt?: string } | null)?.issuedAt;
  if (issued) items.push(["Issued (signed document)", <span key="i" className="num">{issued}</span>]);
  return (
    <dl className="m-0 grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">{k}</dt>
          <dd className="m-0 mt-0.5">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function VerifyPage() {
  const params = useParams<{ batchId: string }>();
  const dep = useDeployment();
  const batchId = useMemo(() => {
    try {
      return /^\d+$/.test(params.batchId) ? BigInt(params.batchId) : null;
    } catch {
      return null;
    }
  }, [params.batchId]);

  const cert = useCertificate(batchId);
  const alloc = useAllocationsForBatch(batchId ?? undefined);
  const [key, setKey] = useFragmentKey(params.batchId);
  const keyId = useMemo(() => (key ? bytesToB64(key) : ""), [key]);
  const [pasted, setPasted] = useState("");
  const [editText, setEditText] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<VerifyReport | null>(null);
  const [tampered, setTampered] = useState<VerifyReport | null>(null);

  const b = cert.data?.batch;
  const onchain: OnchainBatch | null = useMemo(
    () =>
      b
        ? {
            commitment: b.commitment,
            attestor: b.attestor,
            pqSigHash: b.pqSigHash,
            cidDigest: b.cidDigest,
            installationId: b.installationId,
            period: b.period,
            seeDeclared: b.seeDeclared,
            verifiedTonnes: b.verifiedTonnes,
            riskScore: b.riskScore,
            modelVersionHash: b.modelVersionHash,
            algId: b.algId,
          }
        : null,
    [b],
  );
  const bytes = cert.data?.bundleBytes;
  const attestorAddr = b?.attestor;
  // EIP-712 domain: this chain + the attestation proxy; also the binding target of the fields-match row
  const context = useMemo(() => (dep ? { chainId: CHAIN_ID, attestationContract: dep.attestation as Address } : null), [dep]);
  // registry.isAttestorPqKey(attestor, keccak256(bundle public key))
  const isPqKeyAnchored = useMemo(
    () => (dep && attestorAddr ? (h: Hex) => readIsAttestorPqKey(publicClient, dep, attestorAddr, h) : undefined),
    [dep, attestorAddr],
  );
  const revocation = useQuery({
    queryKey: ["chain", "revocation", dep?.attestation, batchId?.toString(), b?.status],
    enabled: !!dep && batchId !== null && b?.statusName === "Rejected",
    refetchInterval: 8000,
    queryFn: () => readBatchRevocation(publicClient, dep!, batchId!),
  });

  // (e) a different batch or a different key is a different certificate: drop every edit and derived result first
  useEffect(() => {
    setEditText(null);
    setTampered(null);
    setBaseline(null);
    setPasted("");
  }, [params.batchId, keyId]);

  // 1) the untampered checks (public ones always; the keyed ones when the fragment key is present)
  useEffect(() => {
    let live = true;
    if (!onchain || !bytes || !context) return;
    verifyBundle({ bundleBytes: bytes, onchain, key, context, isPqKeyAnchored }).then((r) => live && setBaseline(r));
    return () => {
      live = false;
    };
  }, [bytes, onchain, key, context, isPqKeyAnchored]);

  const original = useMemo(() => (baseline?.document !== undefined ? JSON.stringify(baseline.document, null, 2) : null), [baseline]);
  const edited = editText !== null && original !== null && editText !== original;
  const parsed = useMemo(() => {
    if (!edited) return { ok: true as const, doc: undefined };
    try {
      return { ok: true as const, doc: JSON.parse(editText!) as unknown };
    } catch {
      return { ok: false as const, doc: undefined };
    }
  }, [edited, editText]);

  // 2) the same pipeline over the edited copy, re-run on every edit
  useEffect(() => {
    let live = true;
    if (!edited || !parsed.ok || !onchain || !bytes || !context) {
      setTampered(null);
      return;
    }
    verifyBundle({ bundleBytes: bytes, onchain, key, documentOverride: parsed.doc, context, isPqKeyAnchored }).then((r) => live && setTampered(r));
    return () => {
      live = false;
    };
  }, [edited, parsed, bytes, onchain, key, context, isPqKeyAnchored]);

  const live = edited && parsed.ok ? tampered : baseline;
  const sig = signatureStatuses(live);
  const score = asScore(baseline?.document);
  const accounts = dep?.accounts as Record<string, string | undefined> | undefined;

  const unseal = () => {
    const k = parseKeyFromHash(`k=${pasted.trim()}`);
    if (!k) return;
    window.history.replaceState(null, "", `${window.location.pathname}#k=${pasted.trim()}`);
    setKey(k);
  };

  const instName = b ? INSTALLATION_NAMES[b.installation] : undefined;
  const band = b ? bandFor(b.riskScore) : undefined;
  const revoked = revocation.data ?? null;
  const shownReason: Hex = revoked && revoked.reasonHash !== ZERO32 ? revoked.reasonHash : (b?.reasonHash ?? ZERO32);

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <PageHeader eyebrow="Public certificate · no wallet needed" title={batchId === null ? "Certificate" : `Batch #${batchId.toString()}`}>
        Every check on this page runs in your browser against the chain: nothing here asks you to trust EmbedProof&apos;s server.
      </PageHeader>

      <NeedsDeployment>
        {batchId === null && <Notice tone="red" title="Not a batch id">The URL should look like /verify/1.</Notice>}
        {cert.isLoading && <Spinner label="Reading the chain…" />}
        {cert.isError && (
          <Notice tone="red" title={`No batch #${params.batchId} on this chain`}>
            The contract has no such batch (or the RPC at {publicClient.chain.rpcUrls.default.http[0]} is unreachable).
          </Notice>
        )}

        {b && cert.data && (
          <>
            {/* ---------------- headline ---------------- */}
            <Glass>
              <div className="grid gap-6 md:grid-cols-[1.2fr_1fr] md:items-center">
                <div>
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <StatusChip status={b.statusName} />
                    {revoked && <Chip tone="red">Revoked by admin</Chip>}
                    <BandChip band={band} />
                    {live?.allValid && <Chip tone="green">✓ all checks passed</Chip>}
                    {live?.anyFailed && <Chip tone="red">✕ verification failed</Chip>}
                    {live?.sealed && !live.anyFailed && <Chip>🔒 report sealed</Chip>}
                  </div>
                  <p className="text-xs font-bold uppercase tracking-[0.14em] text-white/75">Declared specific embedded emissions</p>
                  <p className="flex flex-wrap items-baseline gap-x-3">
                    <span className="num text-6xl sm:text-7xl" style={{ color: band === "green" ? "var(--color-teal-text)" : band === "amber" ? "var(--color-amber-text)" : "var(--color-rose-text)" }}>
                      {formatSee(b.seeDeclared)}
                    </span>
                    <span className="text-lg text-[var(--color-mute)]">tCO₂ per tonne of product</span>
                  </p>
                  <p className="mt-3 text-lg font-bold">
                    {b.installation}
                    {instName && <span className="font-normal text-[var(--color-mute)]"> · {instName}</span>}
                  </p>
                  <p className="text-[var(--color-mute)]">
                    Period {uint32ToPeriod(b.period)} · <span className="num">{formatTonnes(b.verifiedTonnes)}</span> verified tonnes
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <Tile>
                    <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">AI risk score</p>
                    <p className="num text-4xl">{b.riskScore}<span className="text-lg text-white/75">/100</span></p>
                    <p className="text-xs text-white/75">green ≤ 30 · amber 31 to 60 · red &gt; 60</p>
                  </Tile>
                  <Tile>
                    <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Verifier</p>
                    {revoked ? (
                      <>
                        <p className="text-lg font-bold">Revoked</p>
                        <p className="mono text-white/80">by {labelAddress(revoked.by, accounts) ?? shortAddr(revoked.by)}</p>
                      </>
                    ) : b.verifier !== zeroAddress ? (
                      <>
                        <p className="text-lg font-bold">{b.statusName === "Rejected" ? "Overridden" : "Co-signed"}</p>
                        <p className="mono text-white/80">{labelAddress(b.verifier, accounts) ?? shortAddr(b.verifier)}</p>
                      </>
                    ) : b.statusName === "PendingVerifier" ? (
                      <p className="text-base font-bold text-[var(--color-amber-text)]">Waiting for a human</p>
                    ) : (
                      <p className="text-base font-bold">Not needed <span className="block text-xs font-normal text-white/75">risk below threshold</span></p>
                    )}
                  </Tile>
                  {shownReason !== ZERO32 && (
                    <Tile className="col-span-2">
                      <p className="mb-1 text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">{revoked ? "Admin's reason for revoking" : "Verifier's reason"}</p>
                      <ReasonText hash={shownReason} />
                    </Tile>
                  )}
                </div>
              </div>
            </Glass>

            {revoked && (
              <Notice tone="red" title="This batch has been revoked">
                The signatures below still prove what was attested, but the contract no longer treats these tonnes as verified: they cannot be allocated.{" "}
                <TxLink hash={revoked.txHash} />
              </Notice>
            )}

            {/* ---------------- checks ---------------- */}
            <Glass>
              <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <div>
                  <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--color-teal-text)]">Verify</p>
                  <h2 className="text-2xl font-bold sm:text-3xl">Both signatures, checked here and now</h2>
                </div>
                {edited && <Chip tone="red">checking an edited copy</Chip>}
              </div>

              {cert.data.bundleError && (
                <div className="mb-4">
                  <Notice tone="red" title="Could not fetch the signed bundle">
                    {cert.data.bundleError}. Expected at <span className="mono">{bundleUrl(cert.data.cid)}</span>. On-chain data is still shown, but no signature can be checked.
                  </Notice>
                </div>
              )}

              <div className="mb-4 grid gap-3 sm:grid-cols-2">
                <SigBadge
                  label="ECDSA secp256k1 · attestor"
                  status={sig.ecdsa}
                  note={live?.recoveredSignerOnchain ? `signer ${shortAddr(live.recoveredSignerOnchain)}${live.sealed ? " · over the on-chain commitment" : ""}` : undefined}
                />
                <SigBadge
                  label="ML-DSA-44 · post-quantum"
                  status={sig.mldsa}
                  note={
                    live?.sealed
                      ? "sealed: open with the certificate link"
                      : live?.bundle
                        ? `public key ${MLDSA44_PK_BYTES.toLocaleString()} B · signature ${MLDSA44_SIG_BYTES.toLocaleString()} B`
                        : undefined
                  }
                />
              </div>

              <CheckGroups report={bytes ? live : null} />

              {!key && bytes && (
                <div className="tile mt-4 flex flex-col gap-3 p-4">
                  <p className="font-bold">The report itself is sealed.</p>
                  <p className="text-sm text-[var(--color-mute)]">
                    Open this page from the certificate link or QR code (it ends in <span className="mono">#k=…</span>) to unseal it, re-check the post-quantum signature and run the tamper test.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <label className="sr-only" htmlFor="pasted-key">Certificate key</label>
                    <input id="pasted-key" className="inp mono !w-auto min-w-0 flex-1 !text-sm" placeholder="paste the key after #k= here" value={pasted} onChange={(e) => setPasted(e.target.value)} />
                    <button className="btn btn-primary" onClick={unseal} disabled={!parseKeyFromHash(`k=${pasted.trim()}`)}>
                      Unseal
                    </button>
                  </div>
                </div>
              )}
            </Glass>

            {/* ---------------- why this score (needs the key) ---------------- */}
            {score && (
              <Glass>
                <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--color-teal-text)]">AI plausibility</p>
                <h2 className="mb-4 text-2xl font-bold sm:text-3xl">Why the model scored it this way</h2>
                <PlausibilityView score={score} seeDeclared={score.seeDeclared} />
              </Glass>
            )}

            {/* ---------------- tamper test ---------------- */}
            {original && (
              <Glass>
                <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--color-rose-text)]">Tamper test</p>
                <h2 className="mb-1 text-2xl font-bold sm:text-3xl">Edit one digit. Watch the proof break.</h2>
                <p className="mb-4 max-w-3xl text-[var(--color-mute)]">
                  This is the real decrypted report. Change anything in it and every check above re-runs live: the fingerprint no longer matches the chain, and neither signature validates.
                </p>
                <TamperPanel original={original} editText={editText} setEditText={setEditText} live={live} />
              </Glass>
            )}

            {/* ---------------- allocations ---------------- */}
            <Glass>
              <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--color-teal-text)]">Allocation ledger</p>
              <h2 className="mb-4 text-2xl font-bold sm:text-3xl">Who holds these tonnes</h2>
              {alloc.isLoading ? <Spinner /> : <AllocationTable rows={alloc.data ?? []} verified={b.verifiedTonnes} accounts={accounts} />}
            </Glass>

            {/* ---------------- details + qr ---------------- */}
            <Glass>
              <div className="grid gap-8 lg:grid-cols-[1.4fr_1fr]">
                <div>
                  <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--color-teal-text)]">On-chain record</p>
                  <h2 className="mb-4 text-2xl font-bold">Technical details</h2>
                  <Details b={b} cid={cert.data.cid} accounts={accounts} doc={baseline?.document} />
                </div>
                <QrCode />
              </div>
            </Glass>
          </>
        )}
      </NeedsDeployment>
    </div>
  );
}
