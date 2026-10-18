"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PlausibilityView } from "@/components/Plausibility";
import { Rack } from "@/components/Rack";
import { ActAs, BandChip, Chip, Field, Mono, Notice, Spinner, StatusChip, Tile, TxLink, cx } from "@/components/ui";
import { ApiError, attestorApi, fetchBundleBytes } from "@/lib/attestor";
import { getDemoReports } from "@/lib/deployments";
import { DEMO_MODE } from "@/lib/env";
import { decodeError, formatArgs } from "@/lib/errors";
import { useInstallations, useRefreshChain } from "@/lib/hooks";
import {
  computeIndirect,
  computeSee,
  formatTonnes,
  reportHash,
  shortAddr,
  type AttestResult,
  type Report,
  type ScoreResult,
} from "@/lib/report";
import { useSigner } from "@/lib/signer";
import { b64ToBytes, parseBundle } from "@/lib/verify";

interface FormState {
  installationId: string;
  period: string;
  cnCode: string;
  production: string;
  direct: string;
  electricity: string;
  grid: string;
}

const toForm = (r: Report): FormState => ({
  installationId: r.installationId,
  period: r.period,
  cnCode: r.cnCode,
  production: String(r.productionTonnes),
  direct: String(r.directEmissionsTco2),
  electricity: String(r.electricityMwh),
  grid: String(r.gridFactorTco2PerMwh),
});

const num = (s: string) => (s.trim() === "" ? NaN : Number(s));

function ErrorBox({ err }: { err: unknown }) {
  if (!err) return null;
  if (err instanceof ApiError) {
    return (
      <Notice tone="red" title={err.revert ? `Contract reverted: ${err.revert.name}` : `Attestor said no (${err.status || "offline"})`}>
        {err.detail}
        {err.revert && err.revert.args.length > 0 && <span className="mono block">args: {formatArgs(err.revert.args)}</span>}
      </Notice>
    );
  }
  const d = decodeError(err);
  return (
    <Notice tone="red" title={d.userRejected ? "Signature rejected" : d.name !== "Error" ? `Reverted: ${d.name}` : "Something failed"}>
      {d.message}
      {d.args.length > 0 && <span className="mono block">args: {formatArgs(d.args)}</span>}
    </Notice>
  );
}

export function AttestFlow({ onAllocate }: { onAllocate?: (batchId: string) => void }) {
  const signer = useSigner();
  const refresh = useRefreshChain();
  const inst = useInstallations();
  const demo = useMemo(() => getDemoReports(), []);

  const [form, setForm] = useState<FormState>(() => toForm(demo.honest));
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [score, setScore] = useState<ScoreResult | null>(null);
  const [result, setResult] = useState<AttestResult | null>(null);
  const [pqSig, setPqSig] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "score" | "sign" | "submit">(null);
  const [err, setErr] = useState<unknown>(null);

  const instView = inst.data?.find((i) => i.idStr === form.installationId);
  const prod = num(form.production);
  const direct = num(form.direct);
  const elec = num(form.electricity);
  const grid = num(form.grid);
  const indirect = computeIndirect(elec, grid);
  const see = computeSee(direct, indirect, prod);

  const report: Report | null = useMemo(() => {
    if (!instView) return null;
    if (!(prod > 0) || ![direct, elec, grid].every((x) => Number.isFinite(x) && x >= 0)) return null;
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(form.period) || !/^\d{4,10}$/.test(form.cnCode)) return null;
    return {
      schema: "embedproof.report.v1",
      installationId: form.installationId,
      period: form.period,
      route: instView.route,
      cnCode: form.cnCode,
      productionTonnes: prod,
      directEmissionsTco2: direct,
      electricityMwh: elec,
      gridFactorTco2PerMwh: grid,
      indirectEmissionsTco2: indirect,
      seeTco2PerT: see,
    };
  }, [instView, form.installationId, form.period, form.cnCode, prod, direct, elec, grid, indirect, see]);

  const isOperator = !!instView && !!signer.address && instView.operator.toLowerCase() === signer.address.toLowerCase();
  const myInstallations = useMemo(
    () => inst.data?.filter((i) => signer.address && i.operator.toLowerCase() === signer.address.toLowerCase()) ?? [],
    [inst.data, signer.address],
  );

  // keep the form on a real installation once the registry has loaded
  useEffect(() => {
    if (inst.data && inst.data.length > 0 && !inst.data.some((i) => i.idStr === form.installationId)) {
      setForm((f) => ({ ...f, installationId: (myInstallations[0] ?? inst.data![0]).idStr }));
    }
  }, [inst.data, myInstallations, form.installationId]);

  const edit = (patch: Partial<FormState>) => {
    setForm((f) => ({ ...f, ...patch }));
    setScore(null);
    setResult(null);
    setErr(null);
    setStep(1);
  };

  const prefill = (r: Report) => {
    edit(toForm(r));
    if (DEMO_MODE) {
      const op = inst.data?.find((i) => i.idStr === r.installationId)?.operator;
      const p = signer.personas.find((x) => op && x.address.toLowerCase() === op.toLowerCase());
      if (p) signer.selectPersona(p.key);
    }
  };

  const check = async () => {
    if (!report) return;
    setErr(null);
    setBusy("score");
    try {
      setScore(await attestorApi.score(report));
      setStep(2);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const submit = async () => {
    if (!report) return;
    setErr(null);
    try {
      setBusy("sign");
      const sig = await signer.signRaw(reportHash(report));
      setBusy("submit");
      const res = await attestorApi.attest(report, sig);
      setResult(res);
      setStep(3);
      refresh();
      fetchBundleBytes(res.cid)
        .then((b) => setPqSig(parseBundle(b).mldsa.signature))
        .catch(() => setPqSig(null));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const field = (k: keyof FormState) => ({ value: form[k], onChange: (e: React.ChangeEvent<HTMLInputElement>) => edit({ [k]: e.target.value } as Partial<FormState>) });

  return (
    <div className="flex flex-col gap-5">
      <ActAs keys={["exporterA", "exporterB"]} need={!!signer.address && !!inst.data && myInstallations.length === 0} reason="Reports are submitted by an installation's registered operator." />

      {/* ---------------- 1. report ---------------- */}
      <Rack
        n={1}
        title="Monthly report"
        active={step === 1}
        done={step > 1}
        onSelect={() => setStep(1)}
        summary={report ? `${report.installationId} · ${report.period} · SEE ${see.toFixed(4)} tCO₂/t` : "incomplete"}
      >
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="hint mr-1 font-bold">Prefill the demo:</span>
            <button className="btn btn-ghost btn-sm" onClick={() => prefill(demo.honest)}>
              Honest EAF mill
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => prefill(demo.implausible)}>
              Implausible BF-BOF claim
            </button>
            {demo.amber && (
              <button className="btn btn-ghost btn-sm" onClick={() => prefill(demo.amber!)}>
                Amber EAF claim
              </button>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Installation" htmlFor="f-inst" hint={instView ? `${instView.route} route · ${instView.country} · ${formatTonnes(instView.capacityTpy)} t/yr · operator ${shortAddr(instView.operator)}` : "Registered installations only"}>
              <select id="f-inst" className="inp" value={form.installationId} onChange={(e) => edit({ installationId: e.target.value })}>
                {(inst.data ?? []).map((i) => (
                  <option key={i.id} value={i.idStr}>
                    {i.idStr} {signer.address && i.operator.toLowerCase() === signer.address.toLowerCase() ? "· yours" : ""}
                  </option>
                ))}
                {inst.data?.length === 0 && <option value="">no installations yet</option>}
              </select>
            </Field>
            <Field label="Reporting period" htmlFor="f-period" hint="YYYY-MM">
              <input id="f-period" className="inp" inputMode="numeric" placeholder="2026-09" {...field("period")} />
            </Field>
            <Field label="CN code" htmlFor="f-cn" hint="Combined Nomenclature product code">
              <input id="f-cn" className="inp" inputMode="numeric" {...field("cnCode")} />
            </Field>
            <Field label="Production (tonnes of product)" htmlFor="f-prod">
              <input id="f-prod" className="inp" inputMode="decimal" {...field("production")} />
            </Field>
            <Field label="Direct emissions (tCO₂)" htmlFor="f-direct">
              <input id="f-direct" className="inp" inputMode="decimal" {...field("direct")} />
            </Field>
            <Field label="Electricity used (MWh)" htmlFor="f-elec">
              <input id="f-elec" className="inp" inputMode="decimal" {...field("electricity")} />
            </Field>
            <Field label="Grid factor (tCO₂ / MWh)" htmlFor="f-grid">
              <input id="f-grid" className="inp" inputMode="decimal" {...field("grid")} />
            </Field>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Tile>
              <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Indirect emissions · auto</p>
              <p className="num text-3xl">{Number.isFinite(indirect) ? indirect.toLocaleString("en-US", { maximumFractionDigits: 3 }) : "n/a"} <span className="text-base text-white/75">tCO₂</span></p>
              <p className="hint">electricity × grid factor</p>
            </Tile>
            <Tile>
              <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Declared SEE · auto</p>
              <p className="num text-3xl">{Number.isFinite(see) && prod > 0 ? see.toFixed(4) : "n/a"} <span className="text-base text-white/75">tCO₂ / t</span></p>
              <p className="hint">(direct + indirect) ÷ production</p>
            </Tile>
          </div>

          {instView && signer.address && !isOperator && (
            <Notice tone="amber" title="You are not this installation's operator">
              The attestor only accepts a report signed by the registered operator ({shortAddr(instView.operator)}).
            </Notice>
          )}
          <ErrorBox err={step === 1 ? err : null} />
          <div className="flex flex-wrap items-center gap-3">
            <button className="btn btn-primary" onClick={check} disabled={!report || busy !== null}>
              {busy === "score" ? <Spinner label="Scoring…" /> : "Check plausibility →"}
            </button>
            {!report && <span className="hint">Fill every field with valid numbers to continue.</span>}
          </div>
        </div>
      </Rack>

      {/* ---------------- 2. plausibility ---------------- */}
      <Rack
        n={2}
        title="AI plausibility check"
        active={step === 2}
        done={step > 2}
        tone={score && step === 2 ? score.band === "red" ? "red" : score.band === "amber" ? "amber" : undefined : undefined}
        onSelect={score ? () => setStep(2) : undefined}
        summary={score ? `Risk ${score.riskScore}/100 · ${score.band}` : "run the check first"}
      >
        {score && report && (
          <div className="flex flex-col gap-5">
            <PlausibilityView score={score} seeDeclared={report.seeTco2PerT} />
            {score.band === "red" && (
              <Notice tone="red" title="Red flag: a verifier must review this before it counts">
                You can still submit. The batch will sit as <strong>PendingVerifier</strong> and cannot be allocated until a human verifier approves it.
              </Notice>
            )}
            {score.band === "amber" && <Notice tone="amber" title="Flagged, but it will be attested">Amber claims go through and stay visible as flagged on the public certificate.</Notice>}
            <div className="tile flex flex-col gap-2 p-4">
              <p className="text-sm text-[var(--color-mute)]">
                You will sign the report&apos;s fingerprint, <span className="font-bold text-white">keccak256(JCS(report))</span>, with your {signer.mode === "demo" ? "demo persona key" : "wallet"} (EIP-191). The attestor checks it
                came from the registered operator, then signs the attestation twice and anchors it on-chain.
              </p>
              <p className="inline-flex items-center gap-2 text-xs text-white/75">
                report fingerprint <Mono value={reportHash(report)} />
              </p>
            </div>
            <ErrorBox err={step === 2 ? err : null} />
            <div className="flex flex-wrap gap-3">
              <button className="btn btn-primary" onClick={submit} disabled={busy !== null || !signer.address}>
                {busy === "sign" ? <Spinner label="Waiting for signature…" /> : busy === "submit" ? <Spinner label="Attesting on-chain…" /> : "Sign & submit →"}
              </button>
              <button className="btn btn-ghost" onClick={() => setStep(1)} disabled={busy !== null}>
                Edit report
              </button>
            </div>
          </div>
        )}
      </Rack>

      {/* ---------------- 3. certificate ---------------- */}
      <Rack
        n={3}
        title="Certificate"
        active={step === 3}
        done={step === 3 && !!result}
        tone={result?.status === "PendingVerifier" ? "amber" : undefined}
        onSelect={result ? () => setStep(3) : undefined}
        summary={result ? `Batch #${result.batchId} · ${result.status}` : "submit to mint"}
      >
        {result && (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-3">
              <StatusChip status={result.status} />
              <BandChip band={result.band} />
              <span className="num text-3xl">Batch #{result.batchId}</span>
            </div>

            {result.status === "PendingVerifier" ? (
              <Notice tone="amber" title="Waiting for a verifier">
                The AI flagged this claim (risk {result.riskScore}). It is anchored on-chain, but its tonnes cannot be allocated until a verifier approves it.{" "}
                <Link className="font-bold underline" href="/verifier">Open the verifier queue →</Link>
              </Notice>
            ) : (
              <Notice tone="green" title="Attested">
                The claim is on-chain and its tonnes can now be allocated to consignments.
              </Notice>
            )}

            <dl className="m-0 grid gap-x-6 gap-y-4 sm:grid-cols-2">
              {(
                [
                  ["Transaction", <TxLink key="tx" hash={result.txHash} />],
                  ["IPFS CID", <Mono key="cid" value={result.cid} />],
                  ["Commitment · keccak256(JCS(document))", <Mono key="c" value={result.commitment} />],
                  ["ML-DSA-44 signature hash (on-chain)", <Mono key="p" value={result.pqSigHash} />],
                  ["ECDSA signature (attestor)", <Mono key="e" value={result.ecdsaSignature} />],
                  [
                    "ML-DSA-44 signature (in the bundle)",
                    pqSig ? (
                      <span key="pq" className="mono">
                        {pqSig.slice(0, 28)}… <span className="text-white/75">({b64ToBytes(pqSig).length.toLocaleString()} bytes)</span>
                      </span>
                    ) : (
                      <span key="pq" className="hint">fetching from the bundle…</span>
                    ),
                  ],
                ] as [string, React.ReactNode][]
              ).map(([k, v]) => (
                <div key={k} className="min-w-0">
                  <dt className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">{k}</dt>
                  <dd className="m-0 mt-1">{v}</dd>
                </div>
              ))}
            </dl>

            <ol className="m-0 grid list-none gap-2 p-0 text-sm sm:grid-cols-3">
              {["Report fingerprinted (JCS + keccak)", "Signed by the operator", "Scored by the AI model", "Signed twice: ECDSA + ML-DSA-44", "Report encrypted, pinned to IPFS", "Anchored on-chain"].map((t) => (
                <li key={t} className="flex items-center gap-2">
                  <span className="grid h-5 w-5 place-items-center rounded-full bg-[var(--color-teal)] text-xs font-bold text-[var(--color-ink)]" aria-hidden>✓</span>
                  {t}
                </li>
              ))}
            </ol>

            <div className="flex flex-wrap gap-3">
              <Link className={cx("btn", "btn-primary")} href={result.verifyPath}>
                Open certificate →
              </Link>
              {result.status === "Attested" && (
                <button className="btn btn-ghost" onClick={() => onAllocate?.(String(result.batchId))}>
                  Allocate tonnes
                </button>
              )}
            </div>
            <p className="hint">
              The link ends in <span className="mono">#k=…</span>: that fragment is the decryption key and never reaches any server. Share it only with people who should see the report.
            </p>
            <Chip>gas used {result.gasUsed?.toLocaleString() ?? "n/a"}</Chip>
          </div>
        )}
      </Rack>
    </div>
  );
}
