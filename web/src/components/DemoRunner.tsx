"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DEMO_SCENARIOS, attestorApi, type DemoRunResult, type DemoScenario } from "@/lib/attestor";
import { describeRunError, explorerTx, stepOutcome } from "@/lib/demoRun";
import { CHAIN_LABEL, DEMO_RUNNER } from "@/lib/env";
import { plain } from "@/lib/report";
import { rememberCert } from "@/lib/sampleCert";
import { Chip, Glass, Mono, Notice, cx } from "./ui";

const SCENARIO_COPY: Record<DemoScenario, { label: string; blurb: string }> = {
  green: { label: "Honest mill", blurb: "A plausible claim: scored green, attested, ready to allocate." },
  red: { label: "Out-of-range claim", blurb: "An EAF-level claim from a blast-furnace mill: scored red, sent to a verifier." },
  "double-claim": { label: "Double claim", blurb: "Allocate the same tonnes twice: the second allocation is rejected by the contract." },
};

/** Skeleton shaped like the result list, so the layout does not jump when the run finishes. */
function RunSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="Running the scenario on-chain" className="flex flex-col gap-3">
      <p className="text-sm text-[var(--color-mute)]">Running on-chain. Each step waits for its transaction, so this takes a few seconds.</p>
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {[72, 56, 64, 48, 60].map((w, i) => (
          <li key={i} className="tile flex animate-pulse items-center gap-3 p-3">
            <span className="h-6 w-24 rounded-full bg-white/20" />
            <span className="h-4 rounded bg-white/20" style={{ width: `${w}%` }} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function Steps({ result }: { result: DemoRunResult }) {
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0">
      {result.steps.map((s, i) => {
        const o = stepOutcome(s);
        const url = s.txHash ? explorerTx(result.chainId, s.txHash) : null;
        return (
          <li key={i} className="tile flex flex-col gap-1 p-3 sm:flex-row sm:items-start sm:gap-3">
            <Chip tone={o.tone} className="shrink-0 self-start">
              {o.text}
            </Chip>
            <div className="min-w-0 flex-1">
              <p className="font-bold">{plain(s.label)}</p>
              {s.detail && <p className="text-sm text-[var(--color-mute)]">{plain(s.detail)}</p>}
              {s.txHash && (
                <p className="mt-1 flex flex-wrap items-center gap-2 text-sm">
                  <Mono value={s.txHash} />
                  {url ? (
                    <a className="font-bold underline decoration-[var(--color-teal)] underline-offset-4" href={url} target="_blank" rel="noreferrer">
                      View on explorer ↗
                    </a>
                  ) : (
                    <span className="text-xs text-white/75">local chain, no explorer</span>
                  )}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Home section: only when NEXT_PUBLIC_DEMO_RUNNER=1 and the attestor's /demo/status says enabled. */
export function DemoRunnerSection() {
  const status = useQuery({ queryKey: ["attestor", "demo-status"], enabled: DEMO_RUNNER, queryFn: attestorApi.demoStatus, retry: false, staleTime: 30_000 });
  const [running, setRunning] = useState<DemoScenario | null>(null);
  const [result, setResult] = useState<DemoRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(() => () => void (live.current = false), []);

  if (!DEMO_RUNNER || !status.data?.enabled) return null;
  const chainName = status.data.chainName ?? CHAIN_LABEL;

  const run = async (s: DemoScenario) => {
    setRunning(s);
    setResult(null);
    setError(null);
    try {
      const r = await attestorApi.demoRun(s);
      if (!live.current) return;
      setResult(r);
      if (r.certificatePath) rememberCert(r.certificatePath);
    } catch (e) {
      if (live.current) setError(describeRunError(e));
    } finally {
      if (live.current) setRunning(null);
    }
  };

  return (
    <Glass>
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-2xl font-bold sm:text-3xl">Run a live scenario on {chainName}</h2>
          <p className="mt-1 max-w-3xl text-[var(--color-mute)]">The attestor plays the whole scenario on this chain with its own demo accounts. Nothing here touches your wallet.</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {DEMO_SCENARIOS.map((s) => (
            <button
              key={s}
              type="button"
              className={cx("tile flex flex-col items-start gap-1 p-4 text-left transition-colors hover:bg-white/10", running === s && "border-white/60 bg-white/10")}
              onClick={() => run(s)}
              disabled={running !== null}
            >
              <span className="text-lg font-bold">{SCENARIO_COPY[s].label}</span>
              <span className="text-sm text-[var(--color-mute)]">{SCENARIO_COPY[s].blurb}</span>
            </button>
          ))}
        </div>

        {running && <RunSkeleton />}
        {error && <Notice tone="red" title="The scenario did not run">{error}</Notice>}
        {result && !running && (
          <div className="flex flex-col gap-3">
            <Steps result={result} />
            {result.certificatePath && (
              <div>
                <Link className="btn btn-primary" href={result.certificatePath}>
                  Open certificate
                </Link>
              </div>
            )}
          </div>
        )}
      </div>
    </Glass>
  );
}
