"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Rack } from "@/components/Rack";
import { ActAs, BandChip, Chip, ConsignmentId, Glass, NeedsDeployment, Notice, PageHeader, Spinner, StatusChip, Tile, TxLink } from "@/components/ui";
import { CbamImpact } from "@/components/CbamImpact";
import { ApiError, attestorApi, disclosureMessage, fetchBundleBytes } from "@/lib/attestor";
import { directIntensity } from "@/lib/cbam";
import { readIsAttestorPqKey } from "@/lib/chainReads";
import { publicClient } from "@/lib/client";
import { plainSummary } from "@/lib/summary";
import { b64ToBytes, parseBundle } from "@/lib/verify";
import { keccak256, type Hex } from "viem";
import type { AllocationView, BatchView } from "@/lib/chainReads";
import { cidFromDigest } from "@/lib/cid";
import { allocatorAbi, getDeployment } from "@/lib/deployments";
import { INSTALLATION_NAMES, labelAddress } from "@/lib/demoMeta";
import { decodeError, formatArgs } from "@/lib/errors";
import { CHAIN_LABEL } from "@/lib/env";
import { useAllocationsForImporter, useBatches, useInstallations, useRefreshChain, useRoleGate } from "@/lib/hooks";
import { bandFor, bytes32ToPrintable, formatSee, formatTonnes, shortAddr, uint32ToPeriod } from "@/lib/report";
import { useSigner, type TxOutcome } from "@/lib/signer";
import type { ScoreResult } from "@/lib/report";

function AllocationPanel({ a, b, active, onSelect, accounts }: { a: AllocationView; b?: BatchView; active: boolean; onSelect: () => void; accounts?: Record<string, string | undefined> }) {
  const signer = useSigner();
  const refresh = useRefreshChain();
  const dep = getDeployment();
  const gate = useRoleGate("importer");
  const installations = useInstallations();
  const installation = installations.data?.find((x) => x.idStr === b?.installation);
  const [unsealed, setUnsealed] = useState<{ keyB64url: string; document: { report?: { directEmissionsTco2?: unknown; productionTonnes?: unknown }; assessment?: ScoreResult } } | null>(null);
  const [busy, setBusy] = useState<null | "claim" | "pdf" | "unseal">(null);
  const [err, setErr] = useState<unknown>(null);
  const [claimTx, setClaimTx] = useState<TxOutcome | null>(null);
  const claimed = a.claimed || !!claimTx;
  const name = bytes32ToPrintable(a.consignmentId) ?? shortAddr(a.consignmentId);

  const claim = async () => {
    if (!dep) return;
    setBusy("claim");
    setErr(null);
    try {
      setClaimTx(await signer.write({ address: dep.allocator, abi: allocatorAbi, functionName: "claim", args: [a.consignmentId] }));
      refresh();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  /** The importer holds an allocation on this batch, so the attestor releases the certificate key to a signed challenge. */
  const unseal = async () => {
    if (!b || !signer.address) throw new Error("No account selected");
    const message = disclosureMessage(b.batchId);
    const signature = await signer.signText(message);
    const got = await attestorApi.disclosure(b.batchId, signer.address, message, signature);
    setUnsealed(got as typeof unsealed & object);
    return got as NonNullable<typeof unsealed>;
  };

  const unsealForReport = async () => {
    setBusy("unseal");
    setErr(null);
    try {
      await unseal();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const pdf = async () => {
    if (!b || !signer.address) return;
    setBusy("pdf");
    setErr(null);
    try {
      const { keyB64url, document } = unsealed ?? (await unseal());
      const cid = cidFromDigest(b.cidDigest);
      // the ML-DSA public key hash printed on the certificate comes from the bundle itself and is checked against the registry
      let pqKeyHash: Hex | undefined;
      let pqAnchored: boolean | undefined;
      try {
        pqKeyHash = keccak256(b64ToBytes(parseBundle(await fetchBundleBytes(cid)).mldsa.publicKey));
        if (dep) pqAnchored = await readIsAttestorPqKey(publicClient, dep, b.attestor, pqKeyHash);
      } catch {
        /* the PDF then says "not available" / "anchor not checked" */
      }
      const { downloadCertificatePdf } = await import("@/lib/pdf");
      await downloadCertificatePdf({
        batch: b,
        allocation: a,
        installationName: INSTALLATION_NAMES[b.installation],
        importerLabel: labelAddress(a.importer, accounts),
        verifyUrl: `${window.location.origin}/verify/${b.batchId}#k=${keyB64url}`,
        chainLabel: CHAIN_LABEL,
        cid,
        txHash: a.txHash,
        explanation: document.assessment ? plainSummary(document.assessment, b.seeDeclared / 1e4) : undefined,
        pqKeyHash,
        pqAnchored,
      });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const dec = err && !(err instanceof ApiError) ? decodeError(err) : null;

  return (
    <Rack
      n={a.cnCode % 100 || "•"}
      title={`${name} · ${formatTonnes(a.tonnes)} t`}
      active={active}
      done={claimed}
      onSelect={onSelect}
      summary={b ? `${b.installation} · batch #${a.batchId} · SEE ${formatSee(b.seeDeclared)} · ${claimed ? "claimed" : "open"}` : `batch #${a.batchId}`}
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          {b && <StatusChip status={b.statusName} />}
          {b && <BandChip band={bandFor(b.riskScore)} />}
          {claimed ? <Chip tone="green">claimed</Chip> : <Chip tone="amber">not yet claimed</Chip>}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Tile>
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Your tonnes</p>
            <p className="num text-3xl">{formatTonnes(a.tonnes)}</p>
            <p className="hint">CN {a.cnCode}</p>
          </Tile>
          <Tile>
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Attested intensity</p>
            <p className="num text-3xl">{b ? formatSee(b.seeDeclared) : "n/a"}</p>
            <p className="hint">tCO₂ per tonne · risk {b?.riskScore ?? "n/a"}/100</p>
          </Tile>
          <Tile>
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Producer</p>
            <p className="text-base font-bold">{b?.installation ?? "n/a"}</p>
            <p className="hint">{b ? `${INSTALLATION_NAMES[b.installation] ?? ""} · ${uint32ToPeriod(b.period)}` : ""}</p>
          </Tile>
        </div>
        <div className="tile p-4 sm:p-5">
          <CbamImpact
            key={`${a.consignmentId}-${unsealed ? "r" : "s"}`}
            installation={installation ? { country: installation.country, route: installation.route } : undefined}
            defaultTonnes={Number(a.tonnes)}
            seeUint={b?.seeDeclared ?? 0}
            reportDirect={directIntensity(unsealed?.document.report)}
          />
          {!unsealed && b && (
            <div className="mt-3">
              <button className="btn btn-ghost btn-sm" onClick={unsealForReport} disabled={busy !== null}>
                {busy === "unseal" ? <Spinner label="Waiting for signature…" /> : "Use direct emissions from the report"}
              </button>
              <span className="hint ml-3">Asks you to sign a short challenge; the attestor releases the key to allocation holders.</span>
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn btn-primary" onClick={claim} disabled={claimed || busy !== null || !gate.allowed || a.importer.toLowerCase() !== signer.address?.toLowerCase()}>
            {busy === "claim" ? <Spinner label="Claiming…" /> : claimed ? "Claimed ✓" : "Claim allocation"}
          </button>
          <button className="btn btn-ghost" onClick={pdf} disabled={busy !== null || !b}>
            {busy === "pdf" ? <Spinner label="Building PDF…" /> : "Download certificate PDF"}
          </button>
          <Link className="btn btn-ghost" href={`/verify/${a.batchId}`}>
            Open public certificate
          </Link>
        </div>
        {claimTx && (
          <Notice tone="green" title="Claimed on-chain">
            <TxLink hash={claimTx.hash} />
          </Notice>
        )}
        {err ? (
          err instanceof ApiError ? (
            <Notice tone="red" title={`Attestor said no (${err.status || "offline"})`}>{err.detail}</Notice>
          ) : dec ? (
            <Notice tone="red" title={dec.userRejected ? "Signature rejected" : `Reverted: ${dec.name}`}>
              {dec.message}
              {dec.args.length > 0 && <span className="mono block">args: {formatArgs(dec.args)}</span>}
            </Notice>
          ) : null
        ) : null}
        <p className="hint">
          Consignment <ConsignmentId id={a.consignmentId} />. The PDF&apos;s QR code carries the full verify link including the decryption key, released to you because you hold an allocation on this batch.
        </p>
      </div>
    </Rack>
  );
}

export default function ImporterPage() {
  const signer = useSigner();
  const dep = getDeployment();
  const accounts = dep?.accounts as Record<string, string | undefined> | undefined;
  const allocs = useAllocationsForImporter(signer.address);
  const batches = useBatches();
  const byId = useMemo(() => new Map((batches.data ?? []).map((b) => [b.batchId.toString(), b])), [batches.data]);
  const [sel, setSel] = useState<string | null>(null);
  const list = allocs.data ?? [];
  const active = sel && list.some((a) => a.consignmentId === sel) ? sel : (list[0]?.consignmentId ?? null);
  const total = list.reduce((s, a) => s + a.tonnes, 0n);
  const claimedT = list.filter((a) => a.claimed).reduce((s, a) => s + a.tonnes, 0n);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Importer" title="Your tonnes, with a record you can re-check">
        Claim what an exporter allocated to you and download a certificate whose QR code lets anyone re-check the attestor&apos;s signatures.
      </PageHeader>
      <ActAs role="importer" />
      <NeedsDeployment>
        {!signer.address ? (
          <Notice tone="neutral" title="Choose an account">Allocations are looked up by your address.</Notice>
        ) : (
          <div className="flex flex-col gap-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <Glass className="!rounded-3xl" pad>
                <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Allocations</p>
                <p className="num text-4xl">{list.length}</p>
              </Glass>
              <Glass className="!rounded-3xl">
                <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Tonnes allocated to you</p>
                <p className="num text-4xl">{formatTonnes(total)}</p>
              </Glass>
              <Glass className="!rounded-3xl">
                <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Claimed</p>
                <p className="num text-4xl">{formatTonnes(claimedT)}<span className="text-lg text-white/75"> t</span></p>
              </Glass>
            </div>
            {allocs.isLoading ? (
              <Spinner />
            ) : list.length === 0 ? (
              <Glass>
                <p className="text-lg font-bold">No allocations for {labelAddress(signer.address, accounts) ?? shortAddr(signer.address)} yet.</p>
                <p className="text-[var(--color-mute)]">An exporter allocates tonnes of an attested batch to your address; they appear here within seconds.</p>
              </Glass>
            ) : (
              list.map((a) => <AllocationPanel key={a.consignmentId} a={a} b={byId.get(a.batchId.toString())} active={active === a.consignmentId} onSelect={() => setSel(a.consignmentId)} accounts={accounts} />)
            )}
          </div>
        )}
      </NeedsDeployment>
    </div>
  );
}
