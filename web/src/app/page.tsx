"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { Chip, Glass, Spinner, cx } from "@/components/ui";
import { ChainPill } from "@/components/Header";
import { CHAIN_LABEL, DEMO_MODE } from "@/lib/env";
import { useAttestorHealth, useChainStatus, useDeployment } from "@/lib/hooks";
import { shortAddr, shortHex } from "@/lib/report";
import { useSigner } from "@/lib/signer";
import type { PersonaKey } from "@/lib/demoAccounts";

const FLOWS: { n: number; title: string; who: string; body: string; href: string; persona: PersonaKey; cta: string }[] = [
  {
    n: 1,
    title: "Attest",
    who: "Exporter",
    body: "The mill submits its monthly report. An AI model checks the claim against what a plant like this can physically emit, then it is signed twice, classically and post-quantum, and anchored on-chain.",
    href: "/exporter",
    persona: "exporterA",
    cta: "Submit a report",
  },
  {
    n: 2,
    title: "Verify signatures",
    who: "Verifier",
    body: "Red flags never pass silently. A human verifier unseals the report, reads the AI's reasons, then co-signs or overrides, with a written reason pinned to the batch.",
    href: "/verifier",
    persona: "verifier",
    cta: "Open the queue",
  },
  {
    n: 3,
    title: "Allocate",
    who: "Exporter",
    body: "Tonnes of a verified batch are assigned to consignments. The contract refuses to hand out more tonnes than were verified: a double claim simply reverts.",
    href: "/exporter#allocate",
    persona: "exporterA",
    cta: "Allocate tonnes",
  },
  {
    n: 4,
    title: "Verify publicly",
    who: "Anyone, no wallet",
    body: "Scan the QR on a certificate. Both signatures are re-checked in your browser. Change one digit of the report and watch every check fail.",
    href: "/verify/1",
    persona: "importerA",
    cta: "Open a certificate",
  },
];

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="tile px-4 py-3">
      <p className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">{label}</p>
      <div className="mt-1 text-base font-bold">{children}</div>
    </div>
  );
}

function LiveStatus() {
  const dep = useDeployment();
  const chain = useChainStatus();
  const health = useAttestorHealth();
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Live chain status">
      <Stat label="Chain">
        <div className="flex items-center gap-2">
          <ChainPill />
        </div>
        {chain.isError && <span className="mt-1 block text-sm font-normal text-[var(--color-rose-text)]">RPC unreachable</span>}
      </Stat>
      <Stat label="Contracts">
        {dep ? (
          <span className="mono !text-sm" title={dep.attestation}>
            registry {shortAddr(dep.registry)}
          </span>
        ) : (
          <span className="text-[var(--color-amber-text)]">not deployed: run make demo</span>
        )}
        {dep && chain.data && (
          <span className="mt-1 block text-sm font-normal text-white/80">
            <span className="num">{chain.data.batchCount}</span> batch{chain.data.batchCount === 1 ? "" : "es"} on-chain
          </span>
        )}
      </Stat>
      <Stat label="Attestor">
        {health.isLoading ? (
          <Spinner label="checking" />
        ) : health.data ? (
          <>
            <span className="text-[var(--color-teal-text)]">online</span>
            <span className="mt-1 block text-sm font-normal text-white/80">
              PQ backend {health.data.pqBackend} · IPFS {health.data.ipfsMode}
            </span>
          </>
        ) : (
          <span className="text-[var(--color-rose-text)]">offline</span>
        )}
      </Stat>
      <Stat label="Model version">
        {health.data?.modelVersionHash ? (
          <span className="mono !text-sm" title={health.data.modelVersionHash}>
            {shortHex(health.data.modelVersionHash, 8, 6)}
          </span>
        ) : (
          <span className="text-white/75">n/a</span>
        )}
      </Stat>
    </div>
  );
}

function FlowCard({ f }: { f: (typeof FLOWS)[number] }) {
  const s = useSigner();
  const router = useRouter();
  const go = () => {
    if (DEMO_MODE) s.selectPersona(f.persona);
    router.push(f.href);
  };
  return (
    <li className="flow-card tile flex flex-col gap-3 p-5">
      <div className="flex items-center justify-between gap-3">
        <span className="num grid h-10 w-10 place-items-center rounded-full border border-white/50 bg-white/10 text-lg" aria-hidden>
          {f.n}
        </span>
        <Chip>{f.who}</Chip>
      </div>
      <h3 className="text-xl font-bold">{f.title}</h3>
      <p className="flex-1 text-sm leading-relaxed text-[var(--color-mute)]">{f.body}</p>
      <button type="button" className={cx("btn btn-sm", f.n === 1 ? "btn-primary" : "btn-ghost")} onClick={go}>
        {f.cta} →
      </button>
    </li>
  );
}

export default function Home() {
  return (
    <div className="flex flex-col gap-8 sm:gap-10">
      <Glass className="!rounded-[36px]">
        <div className="flex flex-col gap-6">
          <div className="flex flex-wrap gap-2">
            <Chip tone="green">IEEE ClimateChain 2026 · Track 1</Chip>
            <Chip>Prototype</Chip>
            <Chip>{CHAIN_LABEL}</Chip>
          </div>
          <h1 className="max-w-4xl text-4xl font-bold leading-[1.05] tracking-tight sm:text-6xl">
            A steel mill&apos;s CO₂ claim you can <span className="text-[var(--color-teal-text)]">check</span>, and a tonne it can&apos;t sell{" "}
            <span className="text-[var(--color-rose-text)]">twice</span>.
          </h1>
          <p className="max-w-3xl text-lg text-[var(--color-mute)] sm:text-xl">
            EmbedProof turns a CO₂-per-tonne figure into a tamper-evident, quantum-safe certificate. AI checks the claim, two signatures seal it, and a smart contract
            makes double-claiming impossible.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link href="/exporter" className="btn btn-primary">
              Start the demo
            </Link>
            <Link href="/verify/1" className="btn btn-ghost">
              Verify a certificate
            </Link>
            <Link href="/regulator" className="btn btn-ghost">
              Regulator view
            </Link>
          </div>
          <LiveStatus />
        </div>
      </Glass>

      <Glass>
        <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--color-teal-text)]">Four flows</p>
            <h2 className="text-2xl font-bold sm:text-3xl">Attest → Verify signatures → Allocate → Verify publicly</h2>
          </div>
          {DEMO_MODE && <p className="hint max-w-sm">Each button switches to the right demo persona for you.</p>}
        </div>
        <ol className="flow-grid grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {FLOWS.map((f) => (
            <FlowCard key={f.n} f={f} />
          ))}
        </ol>
        <p className="mt-5 text-sm text-[var(--color-mute)]">
          What the chain stores is a 32-byte fingerprint of the report, never the report itself. The report stays encrypted; only the certificate link (the QR code) carries the key.
        </p>
      </Glass>
    </div>
  );
}
