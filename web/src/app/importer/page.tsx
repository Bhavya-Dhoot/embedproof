"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Rack } from "@/components/Rack";
import { ActAs, BandChip, Chip, ConsignmentId, Glass, NeedsDeployment, Notice, PageHeader, Spinner, StatusChip, Tile, TxLink } from "@/components/ui";
import { ApiError, attestorApi, disclosureMessage } from "@/lib/attestor";
import type { AllocationView, BatchView } from "@/lib/chainReads";
import { cidFromDigest } from "@/lib/cid";
import { allocatorAbi, getDeployment } from "@/lib/deployments";
import { INSTALLATION_NAMES, labelAddress } from "@/lib/demoMeta";
import { decodeError, formatArgs } from "@/lib/errors";
import { CHAIN_LABEL } from "@/lib/env";
import { useAllocationsForImporter, useBatches, useRefreshChain } from "@/lib/hooks";
import { bandFor, bytes32ToPrintable, formatSee, formatTonnes, shortAddr, uint32ToPeriod } from "@/lib/report";
import { useSigner, type TxOutcome } from "@/lib/signer";

function AllocationPanel({ a, b, active, onSelect, accounts }: { a: AllocationView; b?: BatchView; active: boolean; onSelect: () => void; accounts?: Record<string, string | undefined> }) {
  const signer = useSigner();
  const refresh = useRefreshChain();
  const dep = getDeployment();
  const [busy, setBusy] = useState<null | "claim" | "pdf">(null);
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

  const pdf = async () => {
    if (!b || !signer.address) return;
    setBusy("pdf");
    setErr(null);
    try {
      // the importer holds an allocation on this batch, so the attestor releases the certificate key to a signed challenge
      const message = disclosureMessage(b.batchId);
      const signature = await signer.signText(message);
      const { keyB64url, document } = await attestorApi.disclosure(b.batchId, signer.address, message, signature);
      const { downloadCertificatePdf } = await import("@/lib/pdf");
      await downloadCertificatePdf({
        batch: b,
        allocation: a,
        installationName: INSTALLATION_NAMES[b.installation],
        importerLabel: labelAddress(a.importer, accounts),
        verifyUrl: `${window.location.origin}/verify/${b.batchId}#k=${keyB64url}`,
        chainLabel: CHAIN_LABEL,
        cid: cidFromDigest(b.cidDigest),
        txHash: a.txHash,
        explanation: (document as { assessment?: { explanation?: string } }).assessment?.explanation,
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
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Verified intensity</p>
            <p className="num text-3xl">{b ? formatSee(b.seeDeclared) : "n/a"}</p>
            <p className="hint">tCO₂ per tonne · risk {b?.riskScore ?? "n/a"}/100</p>
          </Tile>
          <Tile>
            <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Producer</p>
            <p className="text-base font-bold">{b?.installation ?? "n/a"}</p>
            <p className="hint">{b ? `${INSTALLATION_NAMES[b.installation] ?? ""} · ${uint32ToPeriod(b.period)}` : ""}</p>
          </Tile>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn btn-primary" onClick={claim} disabled={claimed || busy !== null || a.importer.toLowerCase() !== signer.address?.toLowerCase()}>
            {busy === "claim" ? <Spinner label="Claiming…" /> : claimed ? "Claimed ✓" : "Claim allocation"}
          </button>
          <button className="btn btn-ghost" onClick={pdf} disabled={busy !== null || !b}>
            {busy === "pdf" ? <Spinner label="Building PDF…" /> : "Download certificate PDF"}
          </button>
          <Link className="btn btn-ghost" href={`/verify/${a.batchId}`}>
            Public verify page
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
      <PageHeader eyebrow="Importer" title="Your tonnes, with proof you can show an auditor">
        Claim what an exporter allocated to you and download a certificate whose QR code lets anyone re-verify both signatures.
      </PageHeader>
      <ActAs keys={["importerA", "importerB"]} reason="Importers are ordinary accounts: pick the address the exporter allocated to." />
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
