"use client";

import { useMemo, useState } from "react";
import { CBAM_CAVEAT, CBAM_YEARS, cbamDefaultFor, cbamImpact, type CbamYear } from "@/lib/cbam";
import type { RouteName } from "@/lib/report";
import { Field, Spinner, Tile } from "./ui";

const n1 = (x: number) => x.toLocaleString("en-US", { maximumFractionDigits: 1 });
const signed = (x: number, f: (v: number) => string) => (x < 0 ? `-${f(-x)}` : f(x));

/**
 * CBAM impact illustration: tCO2e counted with the EC default value (plus mark-up) versus this certificate's intensity.
 * Read-only inputs come from the registry (route, country) and the chain (SEE); the user edits tonnes, year and price.
 * The carbon price is never prefilled.
 */
export function CbamImpact({
  installation,
  defaultTonnes,
  seeUint,
  reportDirect,
}: {
  installation?: { country: string; route: RouteName };
  /** tonnes to start from: the batch's attested tonnes, or the allocation's tonnes */
  defaultTonnes: number;
  /** on-chain seeDeclared (tCO2 per t x 1e4), used when the report is not unsealed */
  seeUint: number;
  /** directEmissions / productionTonnes from the decrypted report, when available */
  reportDirect?: number | null;
}) {
  const [tonnes, setTonnes] = useState(String(defaultTonnes));
  const [year, setYear] = useState<CbamYear>(2026);
  const [price, setPrice] = useState("");

  const d = useMemo(() => (installation ? cbamDefaultFor(installation.country, installation.route) : null), [installation]);
  const fromReport = typeof reportDirect === "number" && Number.isFinite(reportDirect);
  const attested = fromReport ? (reportDirect as number) : seeUint / 1e4;
  const priceN = price.trim() === "" ? undefined : Number(price);
  const result = d ? cbamImpact({ tonnes: Number(tonnes), defaultValue: d.direct, attested, year, price: priceN }) : null;

  if (!installation) return <Spinner label="Reading the installation from the registry" />;
  if (!d) {
    return (
      <p className="text-sm text-[var(--color-mute)]">
        No CBAM default value is bundled for the {installation.route} route (only iron and steel routes are covered).
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-xl font-bold sm:text-2xl">Default values versus this certificate</h3>
        <p className="text-sm text-[var(--color-mute)]">How many tCO₂e CBAM would count for these tonnes, with the EC default value and with this certificate&apos;s intensity.</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Tonnes" htmlFor="cbam-t">
          <input id="cbam-t" className="inp" inputMode="decimal" value={tonnes} onChange={(e) => setTonnes(e.target.value.replace(/[^\d.]/g, ""))} />
        </Field>
        <Field label="Import year" htmlFor="cbam-y">
          <select id="cbam-y" className="inp" value={year} onChange={(e) => setYear(Number(e.target.value) as CbamYear)}>
            {CBAM_YEARS.map((y) => (
              <option key={y.year} value={y.year}>
                {y.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Carbon price (€ per tCO₂e)" htmlFor="cbam-p">
          <input id="cbam-p" className="inp" inputMode="decimal" placeholder="enter the CBAM certificate price" value={price} onChange={(e) => setPrice(e.target.value.replace(/[^\d.]/g, ""))} />
        </Field>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Tile>
          <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Default value D</p>
          <p className="num text-3xl">{d.direct.toFixed(3)}</p>
          <p className="hint">
            tCO₂ per tonne, direct emissions, before the mark-up. {d.fallback ? `${d.country} (the workbook's fallback for countries without their own row)` : d.country}, {installation.route}, CN {d.cn}.
          </p>
        </Tile>
        <Tile>
          <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Attested intensity A</p>
          <p className="num text-3xl">{attested.toFixed(fromReport ? 3 : 4)}</p>
          <p className="hint">
            {fromReport
              ? "tCO₂ per tonne: direct emissions divided by production, from the decrypted report (CBAM counts only direct emissions for iron and steel)."
              : "tCO₂ per tonne: SEE incl. indirect, so this understates the gap. Open the certificate link to use direct emissions."}
          </p>
        </Tile>
      </div>

      {result ? (
        <dl className="m-0 flex flex-col gap-2" aria-live="polite">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <dt className="text-[var(--color-mute)]">
              Counted with default values: T × D × (1 + {Math.round(result.markup * 100)}%)
            </dt>
            <dd className="num m-0 text-xl">{n1(result.withDefault)} tCO₂e</dd>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <dt className="text-[var(--color-mute)]">Counted with this certificate: T × A</dt>
            <dd className="num m-0 text-xl">{n1(result.withCertificate)} tCO₂e</dd>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-t border-white/20 pt-2">
            <dt className="font-bold">Difference</dt>
            <dd className="num m-0 text-2xl text-[var(--color-teal-text)]">
              {signed(result.difference, n1)} tCO₂e ({signed(result.differencePct, n1)}%)
            </dd>
          </div>
          {result.euro !== undefined && priceN !== undefined && (
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <dt className="text-[var(--color-mute)]">At the price you entered</dt>
              <dd className="num m-0 text-xl">
                ≈ €{signed(result.euro, (v) => Math.round(v).toLocaleString("en-US"))} gross at €{priceN.toLocaleString("en-US", { maximumFractionDigits: 2 })}/t
              </dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="text-sm text-[var(--color-mute)]">Enter a tonnage to see the comparison.</p>
      )}

      <p className="text-xs leading-relaxed text-white/75">{CBAM_CAVEAT}</p>
    </div>
  );
}
