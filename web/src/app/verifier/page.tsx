"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Hex } from "viem";
import { PlausibilityView } from "@/components/Plausibility";
import { Rack } from "@/components/Rack";
import { ActAs, BandChip, Chip, Glass, NeedsDeployment, Notice, PageHeader, Spinner, StatusChip, Tile, TxLink } from "@/components/ui";
import { ApiError, attestorApi, disclosureMessage } from "@/lib/attestor";
import type { BatchView } from "@/lib/chainReads";
import { attestationAbi, getDeployment } from "@/lib/deployments";
import { INSTALLATION_NAMES, labelAddress } from "@/lib/demoMeta";
import { decodeError, formatArgs, type DecodedError } from "@/lib/errors";
import { useBatches, useDecisions, useRefreshChain, useRoles } from "@/lib/hooks";
import { formatSee, formatTonnes, plain, shortAddr, uint32ToPeriod, type ScoreResult } from "@/lib/report";
import { useSigner, type TxOutcome } from "@/lib/signer";

const REPORT_ORDER = ["installationId", "period", "route", "cnCode", "productionTonnes", "directEmissionsTco2", "electricityMwh", "gridFactorTco2PerMwh", "indirectEmissionsTco2", "seeTco2PerT"];

interface Unsealed {
  keyB64url: string;
  document: { report?: Record<string, unknown>; assessment?: ScoreResult };
}

function ReasonLine({ hash }: { hash: Hex }) {
  const q = useQuery({ queryKey: ["reason", hash], queryFn: () => attestorApi.getReason(hash), retry: false, staleTime: Infinity });
  if (q.isLoading) return <span className="hint">…</span>;
  return q.data ? <q className="italic">{plain(q.data.text)}</q> : <span className="hint">reason text unavailable</span>;
}

function QueueItem({ b, active, onSelect, onDecided, accounts }: { b: BatchView; active: boolean; onSelect: () => void; onDecided: (d: { batchId: bigint; approved: boolean; tx: TxOutcome }) => void; accounts?: Record<string, string | undefined> }) {
  const signer = useSigner();
  const refresh = useRefreshChain();
  const dep = getDeployment();
  const roles = useRoles();
  const [unsealed, setUnsealed] = useState<Unsealed | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<null | "unseal" | "approve" | "reject">(null);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<{ tx: TxOutcome; approved: boolean } | null>(null);

  const unseal = async () => {
    if (!signer.address) return;
    setBusy("unseal");
    setErr(null);
    try {
      const message = disclosureMessage(b.batchId);
      const signature = await signer.signText(message);
      setUnsealed(await attestorApi.disclosure(b.batchId, signer.address, message, signature));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const decide = async (approve: boolean) => {
    if (!dep || !reason.trim()) return;
    setBusy(approve ? "approve" : "reject");
    setErr(null);
    try {
      const { reasonHash } = await attestorApi.postReason(b.batchId, reason.trim());
      const tx = await signer.write({ address: dep.attestation, abi: attestationAbi, functionName: "verifierSign", args: [b.batchId, approve, reasonHash] });
      setDone({ tx, approved: approve });
      onDecided({ batchId: b.batchId, approved: approve, tx });
      refresh();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const score = unsealed?.document.assessment;
  const report = unsealed?.document.report;
  const decoded: DecodedError | null = err && !(err instanceof ApiError) ? decodeError(err) : null;

  return (
    <Rack
      n={`#${b.batchId}`}
      title={`${b.installation} · ${uint32ToPeriod(b.period)}`}
      active={active}
      done={!!done}
      tone={b.riskScore > 60 ? "red" : "amber"}
      onSelect={onSelect}
      summary={`SEE ${formatSee(b.seeDeclared)} · risk ${b.riskScore} · ${formatTonnes(b.verifiedTonnes)} t`}
    >
      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip status={b.statusName} />
          <BandChip risk={b.riskScore} />
          {INSTALLATION_NAMES[b.installation] && <span className="text-sm text-[var(--color-mute)]">{INSTALLATION_NAMES[b.installation]}</span>}
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Tile>
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Declared SEE</p>
            <p className="num text-3xl">{formatSee(b.seeDeclared)}</p>
            <p className="hint">tCO₂ / t</p>
          </Tile>
          <Tile>
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Risk score</p>
            <p className="num text-3xl">{b.riskScore}<span className="text-base text-white/75">/100</span></p>
          </Tile>
          <Tile>
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Tonnes at stake</p>
            <p className="num text-3xl">{formatTonnes(b.verifiedTonnes)}</p>
            <p className="hint">attested by {labelAddress(b.attestor, accounts) ?? shortAddr(b.attestor)}</p>
          </Tile>
        </div>

        {!unsealed ? (
          <div className="tile flex flex-col gap-3 p-4">
            <p className="font-bold">The report is encrypted.</p>
            <p className="text-sm text-[var(--color-mute)]">
              As a verifier you can ask the attestor to unseal it. You prove who you are by signing a short challenge. No password, no gas.
            </p>
            <div>
              <button className="btn btn-primary" onClick={unseal} disabled={busy !== null || !roles.verifier}>
                {busy === "unseal" ? <Spinner label="Waiting for signature…" /> : "Unseal report →"}
              </button>
              {!roles.verifier && <span className="hint ml-3">Needs the VERIFIER role.</span>}
            </div>
          </div>
        ) : (
          <>
            {report && (
              <div className="tile p-4">
                <p className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-white/75">What the mill reported</p>
                <dl className="m-0 grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(report)
                    .filter(([k]) => k !== "schema")
                    .sort(([x], [y]) => (REPORT_ORDER.indexOf(x) + 99) % 99 - ((REPORT_ORDER.indexOf(y) + 99) % 99))
                    .map(([k, v]) => (
                      <div key={k}>
                        <dt className="text-xs text-white/75">{k}</dt>
                        <dd className="num m-0 text-base">{String(v)}</dd>
                      </div>
                    ))}
                </dl>
              </div>
            )}
            {score && <PlausibilityView score={score} seeDeclared={score.seeDeclared} />}
          </>
        )}

        {done ? (
          <Notice tone={done.approved ? "green" : "red"} title={done.approved ? "Co-signed: batch is now Attested" : "Overridden: batch is now Rejected"}>
            <TxLink hash={done.tx.hash} />
          </Notice>
        ) : (
          <div className="flex flex-col gap-3">
            <div>
              <label className="lbl" htmlFor={`reason-${b.batchId}`}>Reason (stored off-chain; its hash is pinned to the batch)</label>
              <textarea
                id={`reason-${b.batchId}`}
                className="inp"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="e.g. Claimed EAF-level intensity for a blast-furnace route with no electric capacity on record."
                maxLength={1000}
              />
            </div>
            {err ? (
              err instanceof ApiError ? (
                <Notice tone="red" title={`Attestor said no (${err.status || "offline"})`}>{err.detail}</Notice>
              ) : decoded ? (
                <Notice tone="red" title={decoded.userRejected ? "Signature rejected" : `Reverted: ${decoded.name}`}>
                  {decoded.message}
                  {decoded.args.length > 0 && <span className="mono block">args: {formatArgs(decoded.args)}</span>}
                </Notice>
              ) : null
            ) : null}
            <div className="flex flex-wrap gap-3">
              <button className="btn btn-primary" onClick={() => decide(true)} disabled={busy !== null || !reason.trim() || !roles.verifier}>
                {busy === "approve" ? <Spinner label="Co-signing…" /> : "Approve & co-sign"}
              </button>
              <button className="btn btn-danger" onClick={() => decide(false)} disabled={busy !== null || !reason.trim() || !roles.verifier}>
                {busy === "reject" ? <Spinner label="Overriding…" /> : "Override (reject)"}
              </button>
            </div>
            {!reason.trim() && <p className="hint">A written reason is required: it is what you will defend later.</p>}
          </div>
        )}
        <Link className="text-sm font-bold underline decoration-[var(--color-teal)] underline-offset-4" href={`/verify/${b.batchId}`}>
          Public page for batch #{b.batchId.toString()} →
        </Link>
      </div>
    </Rack>
  );
}

export default function VerifierPage() {
  const batches = useBatches();
  const decisions = useDecisions();
  const dep = getDeployment();
  const accounts = dep?.accounts as Record<string, string | undefined> | undefined;
  const queue = useMemo(() => (batches.data ?? []).filter((b) => b.statusName === "PendingVerifier"), [batches.data]);
  const [sel, setSel] = useState<bigint | null>(null);
  const [last, setLast] = useState<{ batchId: bigint; approved: boolean; tx: TxOutcome } | null>(null);
  const active = sel !== null && queue.some((b) => b.batchId === sel) ? sel : (queue[0]?.batchId ?? null);
  const byId = useMemo(() => new Map((batches.data ?? []).map((b) => [b.batchId.toString(), b])), [batches.data]);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Verifier" title="The human in the loop">
        The AI never approves what it flags. Red claims land here: unseal the report, read the model&apos;s reasons, then co-sign or override with a written reason.
      </PageHeader>
      <ActAs keys={["verifier"]} reason="Only accounts holding the VERIFIER role can unseal and sign." />
      <NeedsDeployment>
        <div className="flex flex-col gap-5">
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-bold">Queue</h2>
            <Chip tone={queue.length ? "amber" : "green"}>{queue.length} pending</Chip>
          </div>
          {last && (
            <Notice tone={last.approved ? "green" : "red"} title={`Batch #${last.batchId} ${last.approved ? "co-signed, now Attested" : "overridden, now Rejected"}`}>
              <TxLink hash={last.tx.hash} />
            </Notice>
          )}
          {batches.isLoading ? (
            <Spinner />
          ) : queue.length === 0 ? (
            <Glass>
              <p className="text-lg font-bold">Nothing waiting.</p>
              <p className="text-[var(--color-mute)]">Submit the implausible BF-BOF report as Exporter B and it will appear here as PendingVerifier.</p>
            </Glass>
          ) : (
            queue.map((b) => <QueueItem key={b.batchId.toString()} b={b} active={active === b.batchId} onSelect={() => setSel(b.batchId)} onDecided={setLast} accounts={accounts} />)
          )}

          <Glass>
            <h2 className="mb-3 text-2xl font-bold">Decision history</h2>
            {decisions.isLoading ? (
              <Spinner />
            ) : (decisions.data ?? []).length === 0 ? (
              <p className="text-[var(--color-mute)]">No decisions yet.</p>
            ) : (
              <div className="scroll-x">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Batch</th>
                      <th>Decision</th>
                      <th>Verifier</th>
                      <th>Reason</th>
                      <th>Tx</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...decisions.data!].reverse().map((d) => {
                      const b = byId.get(d.batchId.toString());
                      return (
                        <tr key={d.txHash}>
                          <td>
                            <Link className="font-bold underline" href={`/verify/${d.batchId}`}>#{d.batchId.toString()}</Link>
                            {b && <span className="block text-xs text-white/75">{b.installation} · {uint32ToPeriod(b.period)}</span>}
                          </td>
                          <td>{d.approved ? <Chip tone="green">approved</Chip> : <Chip tone="red">overridden</Chip>}</td>
                          <td>{labelAddress(d.verifier, accounts) ?? shortAddr(d.verifier)}</td>
                          <td className="max-w-[28ch]"><ReasonLine hash={d.reasonHash} /></td>
                          <td><span className="mono">{d.txHash.slice(0, 10)}…</span></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Glass>
        </div>
      </NeedsDeployment>
    </div>
  );
}
