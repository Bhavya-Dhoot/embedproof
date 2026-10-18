"use client";

import { useEffect, useState } from "react";
import type { Band, ScoreResult, ShapItem } from "@/lib/report";
import { bandFor, plain } from "@/lib/report";
import { BandChip, cx } from "./ui";

const BAND_COLOR: Record<Band, string> = { green: "#00C2A8", amber: "#FFB547", red: "#FF5C8A" };
const BAND_TEXT: Record<Band, string> = { green: "var(--color-teal-text)", amber: "var(--color-amber-text)", red: "var(--color-rose-text)" };
const BAND_WORD: Record<Band, string> = { green: "Plausible", amber: "Flagged", red: "Implausible" };

const polar = (cx0: number, cy0: number, r: number, v: number) => {
  const t = Math.PI - (v / 100) * Math.PI;
  return [cx0 + r * Math.cos(t), cy0 - r * Math.sin(t)] as const;
};
const arc = (from: number, to: number, r = 90) => {
  const [x1, y1] = polar(120, 120, r, from);
  const [x2, y2] = polar(120, 120, r, to);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
};

/** Semi-circular risk gauge: green 0-30, amber 31-60, red 61-100. */
export function BandGauge({ risk, band }: { risk: number; band?: Band }) {
  const b = band ?? bandFor(risk);
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const t = requestAnimationFrame(() => setShown(risk));
    return () => cancelAnimationFrame(t);
  }, [risk]);
  return (
    <figure className="m-0 flex flex-col items-center" aria-label={`Risk score ${risk} of 100, ${b} band`}>
      <svg viewBox="0 0 240 150" className="w-full max-w-[300px]" role="img" aria-hidden>
        <path d={arc(0, 100)} fill="none" stroke="rgb(255 255 255 / .12)" strokeWidth="20" strokeLinecap="butt" />
        <path d={arc(0, 30)} fill="none" stroke={BAND_COLOR.green} strokeWidth="16" opacity={b === "green" ? 1 : 0.55} />
        <path d={arc(30.6, 60)} fill="none" stroke={BAND_COLOR.amber} strokeWidth="16" opacity={b === "amber" ? 1 : 0.55} />
        <path d={arc(60.6, 100)} fill="none" stroke={BAND_COLOR.red} strokeWidth="16" opacity={b === "red" ? 1 : 0.55} />
        <g style={{ transform: `rotate(${shown * 1.8}deg)`, transformOrigin: "120px 120px", transition: "transform 1.1s cubic-bezier(.2,.8,.2,1)" }}>
          <line x1="120" y1="120" x2="40" y2="120" stroke="#fff" strokeWidth="4" strokeLinecap="round" />
        </g>
        <circle cx="120" cy="120" r="9" fill="#fff" />
        <circle cx="120" cy="120" r="4" fill="#0B1020" />
        <text x="26" y="142" fontSize="11" fill="rgb(255 255 255 / .8)" textAnchor="middle">0</text>
        <text x="214" y="142" fontSize="11" fill="rgb(255 255 255 / .8)" textAnchor="middle">100</text>
      </svg>
      <figcaption className="-mt-1 text-center">
        <span className="num text-5xl" style={{ color: BAND_TEXT[b] }}>
          {risk}
        </span>
        <span className="num ml-1 text-xl text-white/75">/100</span>
        <div className="mt-1 flex justify-center">
          <BandChip band={b} />
        </div>
        <span className="sr-only">{BAND_WORD[b]}</span>
      </figcaption>
    </figure>
  );
}

/** q05-q50-q95 interval with the declared SEE and the CBAM default marked. */
export function IntervalBar({ s, seeDeclared }: { s: Pick<ScoreResult, "q05" | "q50" | "q95" | "cbamDefault" | "band" | "riskScore">; seeDeclared: number }) {
  const band = s.band ?? bandFor(s.riskScore);
  const max = Math.max(s.cbamDefault * 1.08, s.q95 * 1.25, seeDeclared * 1.15, 0.0001);
  const pos = (v: number) => `${Math.min(100, Math.max(0, (v / max) * 100))}%`;
  const near = Math.abs(seeDeclared - s.cbamDefault) / max < 0.16;
  return (
    <div role="img" aria-label={`Expected range ${s.q05.toFixed(3)} to ${s.q95.toFixed(3)}, median ${s.q50.toFixed(3)}. Declared ${seeDeclared.toFixed(3)}. CBAM default ${s.cbamDefault.toFixed(3)}.`}>
      <div className={cx("relative mx-3 sm:mx-6", near ? "h-[88px]" : "h-[64px]")}>
        {/* flags above the bar (the CBAM flag steps up when it would collide with the declared flag) */}
        <div className={cx("absolute z-10 -translate-x-1/2 text-center", near ? "top-[26px]" : "top-0")} style={{ left: pos(seeDeclared) }}>
          <span className="block whitespace-nowrap rounded-md px-1.5 py-0.5 text-[0.7rem] font-bold text-[var(--color-ink)]" style={{ background: BAND_COLOR[band] }}>
            Declared {seeDeclared.toFixed(3)}
          </span>
          <span className="mx-auto block h-0 w-0 border-x-[6px] border-t-[8px] border-x-transparent" style={{ borderTopColor: BAND_COLOR[band] }} />
        </div>
        <div className="absolute top-0 z-10 -translate-x-1/2 text-center" style={{ left: pos(s.cbamDefault) }}>
          <span className="block whitespace-nowrap rounded-md border border-dashed border-[var(--color-amber)] bg-[#0B1020]/85 px-1.5 py-0.5 text-[0.7rem] font-bold text-[var(--color-amber-text)]">
            CBAM default {s.cbamDefault.toFixed(2)}
          </span>
        </div>
        {/* track */}
        <div className={cx("absolute inset-x-0 h-5 rounded-full bg-white/15", near ? "top-[56px]" : "top-[34px]")} />
        <div
          className={cx("absolute h-5 rounded-full", near ? "top-[56px]" : "top-[34px]")}
          style={{ left: pos(s.q05), width: `calc(${pos(s.q95)} - ${pos(s.q05)})`, minWidth: 6, background: "linear-gradient(90deg, rgb(0 194 168 / .55), rgb(59 43 255 / .6))", border: "1px solid rgb(255 255 255 / .55)" }}
        />
        <div className={cx("absolute h-8 w-[3px] -translate-x-1/2 rounded bg-white", near ? "top-[50px]" : "top-[28px]")} style={{ left: pos(s.q50) }} />
        <div className={cx("absolute h-9 w-[3px] -translate-x-1/2 rounded", near ? "top-[48px]" : "top-[26px]")} style={{ left: pos(seeDeclared), background: BAND_COLOR[band] }} />
        <div className={cx("absolute w-0 -translate-x-1/2 border-l-2 border-dashed border-[var(--color-amber)]", near ? "top-[22px] h-[56px]" : "top-[22px] h-[44px]")} style={{ left: pos(s.cbamDefault) }} />
      </div>
      <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 text-xs text-white/85">
        <li className="flex items-center gap-1.5"><span className="inline-block h-2.5 w-6 rounded-full" style={{ background: "linear-gradient(90deg,#00C2A8,#3B2BFF)" }} aria-hidden />model 5 to 95% range <span className="num font-bold text-white">{s.q05.toFixed(3)} to {s.q95.toFixed(3)}</span></li>
        <li className="flex items-center gap-1.5"><span className="inline-block h-3 w-[3px] rounded bg-white" aria-hidden />median <span className="num font-bold text-white">{s.q50.toFixed(3)}</span></li>
      </ul>
      <p className="mt-2 text-xs text-white/75">tCO₂ per tonne of product (specific embedded emissions).</p>
    </div>
  );
}

/** Top-3 SHAP drivers as diverging bars. */
export function ShapBars({ items }: { items: ShapItem[] }) {
  const maxAbs = Math.max(0.0001, ...items.map((i) => Math.abs(i.contribution)));
  return (
    <ul className="m-0 flex list-none flex-col gap-3 p-0">
      {items.slice(0, 3).map((it) => {
        const pct = (Math.abs(it.contribution) / maxAbs) * 50;
        const up = it.contribution >= 0;
        return (
          <li key={it.feature}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
              <span className="font-bold">
                {it.label} <span className="font-normal text-white/80">= {String(it.value)}</span>
              </span>
              <span className="num text-sm" style={{ color: up ? "var(--color-amber-text)" : "var(--color-teal-text)" }}>
                {up ? "+" : "-"}
                {Math.abs(it.contribution).toFixed(3)}
              </span>
            </div>
            <div className="relative h-3 rounded-full bg-white/15" aria-hidden>
              <div className="absolute inset-y-0 left-1/2 w-px bg-white/60" />
              <div
                className="absolute inset-y-0 rounded-full"
                style={{ background: up ? "#FFB547" : "#00C2A8", [up ? "left" : "right"]: "50%", width: `${pct}%`, transition: "width .9s cubic-bezier(.2,.8,.2,1)" }}
              />
            </div>
            <p className="mt-0.5 text-xs text-white/75">{up ? "pushes the expected CO₂ intensity up" : "pulls the expected CO₂ intensity down"}</p>
          </li>
        );
      })}
    </ul>
  );
}

/** The whole plausibility read-out: gauge + interval + SHAP + one-sentence explanation. */
export function PlausibilityView({ score, seeDeclared }: { score: ScoreResult; seeDeclared: number }) {
  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-5 md:grid-cols-[minmax(0,300px)_1fr] md:items-center">
        <BandGauge risk={score.riskScore} band={score.band} />
        <div className="tile p-4 sm:p-5">
          <p className="mb-3 text-xs font-bold uppercase tracking-[0.14em] text-white/75">Declared vs expected</p>
          <IntervalBar s={score} seeDeclared={seeDeclared} />
        </div>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        <div className="tile p-4 sm:p-5">
          <p className="mb-3 text-xs font-bold uppercase tracking-[0.14em] text-white/75">Why: top 3 drivers (SHAP)</p>
          <ShapBars items={score.shap} />
        </div>
        <div className="tile flex flex-col justify-center gap-2 p-4 sm:p-5">
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-white/75">In plain words</p>
          <p className="text-base leading-relaxed">{plain(score.explanation)}</p>
          {score.belowHalfDefault && (
            <p className="text-sm text-[var(--color-amber-text)]">Declared intensity is below half of the CBAM default for this route, so extra scrutiny applies (+20 risk).</p>
          )}
        </div>
      </div>
    </div>
  );
}
