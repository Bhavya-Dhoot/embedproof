"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ActAs, Chip, Glass, NeedsDeployment, PageHeader, Spinner, StatusChip } from "@/components/ui";
import type { BatchView } from "@/lib/chainReads";
import { getDeployment } from "@/lib/deployments";
import { INSTALLATION_NAMES, labelAddress } from "@/lib/demoMeta";
import { useBatches, useInstallations } from "@/lib/hooks";
import { bandFor, formatSee, formatTonnes, shortAddr, uint32ToPeriod } from "@/lib/report";

const COLOR = { green: "#00C2A8", amber: "#FFB547", red: "#FF5C8A" } as const;

function Bars({ rows }: { rows: BatchView[] }) {
  const max = Math.max(...rows.map((r) => r.seeDeclared), 1);
  return (
    <div className="flex h-36 items-end gap-2 overflow-x-auto pb-1" role="img" aria-label="Declared intensity by period">
      {rows.map((r) => (
        <div key={r.batchId.toString()} className="flex w-14 shrink-0 flex-col items-center justify-end gap-1">
          <span className="num text-xs">{formatSee(r.seeDeclared)}</span>
          <div
            className="w-full rounded-t-lg"
            style={{ height: `${Math.max(6, (r.seeDeclared / max) * 84)}px`, background: COLOR[bandFor(r.riskScore)], opacity: r.statusName === "Attested" ? 1 : 0.45, transition: "height .8s cubic-bezier(.2,.8,.2,1)" }}
          />
          <span className="text-[0.65rem] text-white/80">{uint32ToPeriod(r.period)}</span>
        </div>
      ))}
    </div>
  );
}

export default function RegulatorPage() {
  const batches = useBatches();
  const inst = useInstallations();
  const dep = getDeployment();
  const accounts = dep?.accounts as Record<string, string | undefined> | undefined;
  const [onlyAttested, setOnlyAttested] = useState(true);

  const groups = useMemo(() => {
    const m = new Map<string, BatchView[]>();
    for (const b of batches.data ?? []) {
      if (onlyAttested && b.statusName !== "Attested") continue;
      m.set(b.installation, [...(m.get(b.installation) ?? []), b].sort((x, y) => x.period - y.period));
    }
    return [...m.entries()];
  }, [batches.data, onlyAttested]);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Regulator · read-only" title="Attested intensities, per installation, across periods">
        Cross-check TR-ETS reports against CBAM declarations. Everything here is read straight from the chain. There is nothing to approve and nothing to edit.
      </PageHeader>
      <ActAs keys={["regulator"]} reason="The regulator persona has the REGULATOR role; the data below is public regardless." />
      <NeedsDeployment>
        <label className="flex items-center gap-3 text-sm font-bold">
          <input type="checkbox" className="h-5 w-5 accent-[var(--color-teal)]" checked={onlyAttested} onChange={(e) => setOnlyAttested(e.target.checked)} />
          Attested batches only
        </label>
        {batches.isLoading ? (
          <Spinner />
        ) : groups.length === 0 ? (
          <Glass>
            <p className="text-lg font-bold">No {onlyAttested ? "attested " : ""}batches yet.</p>
            <p className="text-[var(--color-mute)]">They appear here the moment an exporter&apos;s report is attested.</p>
          </Glass>
        ) : (
          groups.map(([id, rows]) => {
            const i = inst.data?.find((x) => x.idStr === id);
            return (
              <Glass key={id}>
                <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-2xl font-bold">{id}</h2>
                    <p className="text-sm text-[var(--color-mute)]">
                      {INSTALLATION_NAMES[id] ?? "Installation"} {i && <>· {i.route} · {i.country} · {formatTonnes(i.capacityTpy)} t/yr · operator {labelAddress(i.operator, accounts) ?? shortAddr(i.operator)}</>}
                    </p>
                  </div>
                  <Chip>{rows.length} period{rows.length === 1 ? "" : "s"}</Chip>
                </div>
                <Bars rows={rows} />
                <div className="scroll-x mt-4">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Period</th>
                        <th className="text-right">SEE tCO₂/t</th>
                        <th className="text-right">Tonnes</th>
                        <th className="text-right">Risk</th>
                        <th>Status</th>
                        <th>Verifier</th>
                        <th>Certificate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((b) => (
                        <tr key={b.batchId.toString()}>
                          <td className="num">{uint32ToPeriod(b.period)}</td>
                          <td className="num text-right">{formatSee(b.seeDeclared)}</td>
                          <td className="num text-right">{formatTonnes(b.verifiedTonnes)}</td>
                          <td className="num text-right">{b.riskScore}</td>
                          <td><StatusChip status={b.statusName} /></td>
                          <td>{b.verifier === "0x0000000000000000000000000000000000000000" ? <span className="text-white/75">auto</span> : (labelAddress(b.verifier, accounts) ?? shortAddr(b.verifier))}</td>
                          <td><Link className="font-bold underline decoration-[var(--color-teal)] underline-offset-4" href={`/verify/${b.batchId}`}>#{b.batchId.toString()}</Link></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Glass>
            );
          })
        )}
      </NeedsDeployment>
    </div>
  );
}
