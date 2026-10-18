"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { isAddress, type Address, type Hex } from "viem";
import { Rack } from "@/components/Rack";
import { ActAs, Chip, ConsignmentId, Field, Glass, Notice, Spinner, StatusChip, Tile, TxLink, cx } from "@/components/ui";
import { readRemaining } from "@/lib/chainReads";
import { publicClient } from "@/lib/client";
import { allocatorAbi, getDeployment } from "@/lib/deployments";
import { FALLBACK_IMPORTERS, labelAddress } from "@/lib/demoMeta";
import { decodeError, formatArgs, isDoubleClaim, type DecodedError } from "@/lib/errors";
import { useAllocationsForBatch, useBatches, useInstallations, useRefreshChain, useRoleGate } from "@/lib/hooks";
import { formatSee, formatTonnes, shortAddr, stringToBytes32, uint32ToPeriod } from "@/lib/report";
import { useSigner, type TxOutcome } from "@/lib/signer";

/** The demo's wow moment: a second claim on tonnes that are already spoken for. */
function DoubleClaimBlocked({ err, importer }: { err: DecodedError; importer: string }) {
  const [batchId, requested, remaining] = err.args as [bigint, bigint, bigint];
  return (
    <div className="blocked-halo" role="alert" aria-live="assertive">
      <div className="glass">
        <span className="sheen" aria-hidden />
        <div className="relative z-[1] flex flex-col items-center gap-5 p-6 text-center sm:p-10">
        <svg width="72" height="72" viewBox="0 0 72 72" aria-hidden>
          <path d="M36 4 62 14v20c0 17-11 29-26 34C21 63 10 51 10 34V14L36 4Z" fill="rgb(255 92 138 / .2)" stroke="#FF5C8A" strokeWidth="3" />
          <path d="m26 26 20 20m0-20L26 46" stroke="#FF8FAE" strokeWidth="5" strokeLinecap="round" />
        </svg>
        <p className="text-sm font-bold text-[var(--color-rose-text)]">Rejected by the contract in simulation. Nothing was sent and no gas was spent.</p>
        <h3 className="text-4xl font-bold leading-none tracking-tight sm:text-7xl">Double claim blocked</h3>
        <p className="max-w-2xl text-lg text-white/90">
          Batch <span className="num">#{String(batchId)}</span> has <span className="num">{formatTonnes(remaining)} t</span> left to allocate, and this request was for <span className="num">{formatTonnes(requested)} t</span>. The contract will not allocate the same attested tonnes twice.
        </p>
        <div className="grid w-full max-w-xl grid-cols-2 gap-3">
          <div className="tile p-4">
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Requested</p>
            <p className="num text-4xl text-[var(--color-rose-text)]">{formatTonnes(requested)} <span className="text-lg">t</span></p>
          </div>
          <div className="tile p-4">
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Still unallocated</p>
            <p className="num text-4xl">{formatTonnes(remaining)} <span className="text-lg">t</span></p>
          </div>
        </div>
        <p className="mono text-white/85">
          DoubleClaimBlocked({formatArgs(err.args)}) {importer && <span>· to {importer}</span>}
        </p>
        <p className="text-sm text-white/80">Contract rule: allocated tonnes ≤ attested tonnes, enforced on-chain for every batch.</p>
        </div>
      </div>
    </div>
  );
}

export function AllocateFlow({ preselect }: { preselect?: string }) {
  const signer = useSigner();
  const refresh = useRefreshChain();
  const dep = getDeployment();
  const inst = useInstallations();
  const batches = useBatches();
  const gate = useRoleGate("operator");

  const mine = useMemo(() => {
    const myIds = new Set((inst.data ?? []).filter((i) => signer.address && i.operator.toLowerCase() === signer.address.toLowerCase()).map((i) => i.idStr));
    return (batches.data ?? []).filter((b) => myIds.has(b.installation));
  }, [inst.data, batches.data, signer.address]);

  const remaining = useQuery({
    queryKey: ["chain", "remaining", dep?.allocator, mine.map((b) => `${b.batchId}:${b.status}`).join(",")],
    enabled: !!dep && mine.length > 0,
    refetchInterval: 3000,
    queryFn: async () => Object.fromEntries(await Promise.all(mine.map(async (b) => [b.batchId.toString(), await readRemaining(publicClient, dep!, b.batchId)] as const))),
  });

  const [sel, setSel] = useState<bigint | null>(null);
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [consignment, setConsignment] = useState("");
  const [importer, setImporter] = useState<string>(FALLBACK_IMPORTERS[0].address);
  const [cn, setCn] = useState("72083900");
  const [tonnes, setTonnes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<DecodedError | null>(null);
  const [ok, setOk] = useState<{ tx: TxOutcome; consignment: string; tonnes: string; importer: string } | null>(null);

  const selected = mine.find((b) => b.batchId === sel) ?? null;
  const rem = selected ? remaining.data?.[selected.batchId.toString()] : undefined;
  const history = useAllocationsForBatch(sel ?? undefined);

  // preselect: requested by ?batch= / after a fresh attestation
  useEffect(() => {
    if (sel === null && preselect) {
      const b = mine.find((x) => x.batchId.toString() === preselect && x.statusName === "Attested");
      if (b) {
        setSel(b.batchId);
        setStep(2);
      }
    }
  }, [mine, preselect, sel]);

  const importers = [
    ...(["importerA", "importerB"] as const)
      .map((k, i) => ({ label: `Importer ${i ? "B" : "A"}`, address: dep?.accounts[k] as string | undefined }))
      .filter((x) => !!x.address),
  ];
  const quick = (importers.length ? importers : FALLBACK_IMPORTERS) as { label: string; address: string }[];

  const pick = (id: bigint) => {
    setSel(id);
    setStep(2);
    setErr(null);
    setOk(null);
    setConsignment(`CN-${id}-${String.fromCharCode(65 + ((history.data?.length ?? 0) % 26))}`);
    setTonnes("");
  };

  const tonnesN = Number(tonnes);
  const idOk = (() => {
    try {
      return !!consignment && !!stringToBytes32(consignment);
    } catch {
      return false;
    }
  })();
  const valid = !!selected && selected.statusName === "Attested" && idOk && isAddress(importer) && Number.isInteger(tonnesN) && tonnesN > 0 && /^\d{1,10}$/.test(cn);

  const submit = async () => {
    if (!selected || !valid || !dep) return;
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      const tx = await signer.write({
        address: dep.allocator,
        abi: allocatorAbi,
        functionName: "allocate",
        args: [selected.batchId, stringToBytes32(consignment) as Hex, importer as Address, Number(cn), BigInt(tonnesN)],
      });
      setOk({ tx, consignment, tonnes, importer });
      refresh();
    } catch (e) {
      setErr(decodeError(e));
    } finally {
      setBusy(false);
      setStep(3);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <ActAs role="operator" />

      {/* ---------------- 1. pick a batch ---------------- */}
      <Rack n={1} title="Pick a batch" active={step === 1} done={step > 1} onSelect={() => setStep(1)} summary={selected ? `Batch #${selected.batchId} · ${selected.installation} · ${formatTonnes(rem ?? 0n)} t left` : "choose one of your attested batches"}>
        {batches.isLoading || inst.isLoading ? (
          <Spinner />
        ) : mine.length === 0 ? (
          <Notice tone="amber" title="No batches yet for this account">
            Attest a report first (Attest tab). Only batches of installations you operate show up here.
          </Notice>
        ) : (
          <ul className="m-0 grid list-none gap-3 p-0 sm:grid-cols-2">
            {mine.map((b) => {
              const r = remaining.data?.[b.batchId.toString()];
              const usable = b.statusName === "Attested";
              return (
                <li key={b.batchId.toString()}>
                  <button
                    type="button"
                    className={cx("tile flex w-full flex-col gap-2 p-4 text-left transition-colors hover:bg-white/10", sel === b.batchId && "border-white/60 bg-white/10", !usable && "opacity-70")}
                    onClick={() => usable && pick(b.batchId)}
                    disabled={!usable}
                    aria-pressed={sel === b.batchId}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="num text-xl">Batch #{b.batchId.toString()}</span>
                      <StatusChip status={b.statusName} />
                    </span>
                    <span className="text-sm text-[var(--color-mute)]">
                      {b.installation} · {uint32ToPeriod(b.period)} · SEE <span className="num">{formatSee(b.seeDeclared)}</span>
                    </span>
                    {usable ? (
                      <span>
                        <span className="num text-3xl">{r === undefined ? "…" : formatTonnes(r)}</span> <span className="text-sm text-white/80">of {formatTonnes(b.verifiedTonnes)} attested t remaining</span>
                      </span>
                    ) : (
                      <span className="text-sm text-[var(--color-amber-text)]">{b.statusName === "PendingVerifier" ? "Not allocatable until a verifier approves" : "Rejected: cannot be allocated"}</span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Rack>

      {/* ---------------- 2. allocate ---------------- */}
      <Rack n={2} title="Allocate to a consignment" active={step === 2} done={step > 2 && !!ok} onSelect={selected ? () => setStep(2) : undefined} summary={ok ? `${ok.tonnes} t → ${labelAddress(ok.importer) ?? shortAddr(ok.importer)}` : "importer, CN code, tonnes"}>
        {selected && (
          <div className="flex flex-col gap-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Consignment id" htmlFor="a-cons" hint="Any label up to 32 characters, stored on-chain as a fixed-size id. A consignment id can be used only once.">
                <input id="a-cons" className="inp mono" value={consignment} onChange={(e) => setConsignment(e.target.value)} maxLength={32} />
              </Field>
              <Field label="Tonnes to allocate" htmlFor="a-t" hint={rem !== undefined ? `${formatTonnes(rem)} t unallocated on this batch` : undefined}>
                <input id="a-t" className="inp" inputMode="numeric" placeholder={rem !== undefined ? String(rem) : "5000"} value={tonnes} onChange={(e) => setTonnes(e.target.value.replace(/[^\d]/g, ""))} />
              </Field>
            </div>
            <div>
              <span className="lbl">Importer</span>
              <div className="mb-2 flex flex-wrap gap-2">
                {quick.map((q) => (
                  <button key={q.address} type="button" className={cx("btn btn-sm", importer.toLowerCase() === q.address.toLowerCase() ? "btn-primary" : "btn-ghost")} onClick={() => setImporter(q.address)}>
                    {q.label} · {shortAddr(q.address)}
                  </button>
                ))}
              </div>
              <label className="sr-only" htmlFor="a-imp">Importer address</label>
              <input id="a-imp" className="inp mono" value={importer} onChange={(e) => setImporter(e.target.value.trim())} placeholder="0x…" aria-invalid={!isAddress(importer)} />
            </div>
            <Field label="CN code" htmlFor="a-cn" hint="Product code of the consignment (uint32)">
              <input id="a-cn" className="inp !max-w-xs" inputMode="numeric" value={cn} onChange={(e) => setCn(e.target.value.replace(/[^\d]/g, ""))} />
            </Field>
            <div className="flex flex-wrap items-center gap-3">
              <button className="btn btn-primary" onClick={submit} disabled={!valid || busy || !gate.allowed}>
                {busy ? <Spinner label="Allocating…" /> : "Allocate →"}
              </button>
              <span className="hint">Signs a transaction as {signer.personas.find((p) => p.key === signer.personaKey)?.label ?? shortAddr(signer.address ?? "")}.</span>
            </div>
          </div>
        )}
      </Rack>

      {/* ---------------- 3. outcome ---------------- */}
      <Rack n={3} title="Outcome" active={step === 3} done={!!ok && step === 3} tone={err ? "red" : undefined} onSelect={ok || err ? () => setStep(3) : undefined} summary={err ? err.name : ok ? "allocated" : "n/a"}>
        {err && isDoubleClaim(err) && <DoubleClaimBlocked err={err} importer={labelAddress(importer) ?? shortAddr(importer)} />}
        {err && !isDoubleClaim(err) && (
          <Notice tone="red" title={err.userRejected ? "Signature rejected" : `Reverted: ${err.name}`}>
            {err.message}
            {err.args.length > 0 && <span className="mono block">args: {formatArgs(err.args)}</span>}
          </Notice>
        )}
        {ok && (
          <div className="ok-flash flex flex-col gap-4 rounded-2xl">
            <div className="flex flex-wrap items-center gap-3">
              <Chip tone="green">Allocated</Chip>
              <span className="num text-3xl">{formatTonnes(Number(ok.tonnes))} t</span>
              <span className="text-[var(--color-mute)]">→ {labelAddress(ok.importer) ?? shortAddr(ok.importer)}</span>
            </div>
            <p className="text-sm">
              Consignment <span className="mono">{ok.consignment}</span> is now bound to batch #{selected?.batchId.toString()}. The importer can claim it, and the public certificate lists them as a holder.
            </p>
            <TxLink hash={ok.tx.hash} />
          </div>
        )}
        {(err || ok) && (
          <div className="mt-4 flex flex-wrap gap-3">
            <button className="btn btn-ghost" onClick={() => { setStep(2); setErr(null); }}>
              {err ? "Try another allocation" : "Allocate more"}
            </button>
            {selected && (
              <Link className="btn btn-ghost" href={`/verify/${selected.batchId}`}>
                Open the public certificate
              </Link>
            )}
          </div>
        )}
      </Rack>

      {/* ---------------- ledger for the selected batch ---------------- */}
      {selected && (
        <Glass>
          <h3 className="mb-3 text-lg font-bold">Ledger · batch #{selected.batchId.toString()}</h3>
          {history.data && history.data.length > 0 ? (
            <div className="scroll-x">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Consignment</th>
                    <th>Importer</th>
                    <th className="text-right">Tonnes</th>
                    <th>Claimed</th>
                  </tr>
                </thead>
                <tbody>
                  {history.data.map((a) => (
                    <tr key={a.consignmentId}>
                      <td><ConsignmentId id={a.consignmentId} /></td>
                      <td>{labelAddress(a.importer) ?? shortAddr(a.importer)}</td>
                      <td className="num text-right">{formatTonnes(a.tonnes)}</td>
                      <td>{a.claimed ? "✓" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-[var(--color-mute)]">No allocations yet.</p>
          )}
          <Tile className="mt-3">
            <p className="text-sm">
              <span className="font-bold">{rem !== undefined ? formatTonnes(rem) : "…"} t</span> of {formatTonnes(selected.verifiedTonnes)} attested t remain unallocated.
            </p>
          </Tile>
        </Glass>
      )}
    </div>
  );
}
