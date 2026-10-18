"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { DemoRunnerSection } from "@/components/DemoRunner";
import { Chip, Glass, Spinner, cx } from "@/components/ui";
import { ChainPill } from "@/components/Header";
import { DEMO_MODE } from "@/lib/env";
import { useAttestorHealth, useChainStatus, useDeployment } from "@/lib/hooks";
import { shortAddr, shortHex } from "@/lib/report";
import { sampleTarget, useSampleCert } from "@/lib/sampleCert";
import { useSigner } from "@/lib/signer";
import type { PersonaKey } from "@/lib/demoAccounts";

/** The four flows, in the order a certificate lives them. The same list is used everywhere on the site. */
const FLOWS: { n: number; title: string; who: string; body: string; href: string; persona: PersonaKey; cta: string }[] = [
  {
    n: 1,
    title: "Attest",
    who: "Exporter",
    body: "The mill submits its monthly report. An AI model compares it with the plant's own emissions history, its route and the grid. The attestor signs it with two keys (ECDSA and ML-DSA-44) and anchors it on-chain.",
    href: "/exporter",
    persona: "exporterA",
    cta: "Submit a report",
  },
  {
    n: 2,
    title: "Review",
    who: "Verifier",
    body: "Amber claims are attested with a visible flag. Red claims need a human verifier, who unseals the report, reads the model's reasons, and co-signs or overrides with a written reason.",
    href: "/verifier",
    persona: "verifier",
    cta: "Open the queue",
  },
  {
    n: 3,
    title: "Allocate",
    who: "Exporter",
    body: "The mill assigns attested tonnes to consignments. The contract refuses to allocate more than were attested, so the same tonnes cannot be allocated twice on-chain.",
    href: "/exporter#allocate",
    persona: "exporterA",
    cta: "Allocate tonnes",
  },
  {
    n: 4,
    title: "Verify",
    who: "Anyone, no wallet",
    body: "Open a certificate link or scan its QR code. Signatures and fingerprints are checked in your browser against the chain. Change one digit of the report and the checks fail.",
    href: "/verify",
    persona: "importerA",
    cta: "Verify a certificate",
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

/** Chain, contracts, attestor and model hashes: useful to engineers, noise for everyone else, so it lives behind a disclosure. */
function TechnicalStatus() {
  const dep = useDeployment();
  const chain = useChainStatus();
  const health = useAttestorHealth();
  return (
    <details className="tile">
      <summary className="cursor-pointer p-4 font-bold">Technical status</summary>
      <div className="grid gap-3 p-4 pt-0 sm:grid-cols-2 lg:grid-cols-4" aria-label="Live chain status">
        <Stat label="Chain">
          <ChainPill />
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
                ML-DSA backend {health.data.pqBackend}, bundle store {health.data.ipfsMode}
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
    </details>
  );
}

function FlowCard({ f }: { f: (typeof FLOWS)[number] }) {
  const s = useSigner();
  const router = useRouter();
  const sample = sampleTarget(useSampleCert());
  const href = f.n === 4 ? sample.href : f.href;
  const cta = f.n === 4 && !sample.hasCert ? "Create a certificate first" : f.cta;
  const go = () => {
    if (DEMO_MODE) s.selectPersona(f.n === 4 && !sample.hasCert ? "exporterA" : f.persona);
    router.push(href);
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
        {cta} →
      </button>
    </li>
  );
}

export default function Home() {
  const sample = sampleTarget(useSampleCert());
  return (
    <div className="flex flex-col gap-8 sm:gap-10">
      <Glass className="!rounded-[36px]">
        <div className="flex flex-col gap-6">
          <div>
            <Chip tone="green">ClimateChain 2026 · Prototype</Chip>
          </div>
          <h1 className="max-w-5xl text-4xl font-bold leading-[1.05] tracking-tight sm:text-5xl">
            A CO₂ claim you can <span className="text-[var(--color-teal-text)]">check</span>, and tonnes that cannot be allocated twice.
          </h1>
          <p className="max-w-2xl text-lg text-[var(--color-mute)]">EmbedProof turns a steel mill&apos;s CO₂ figure into a signed, checkable certificate and records who holds each attested tonne.</p>
          <div className="flex flex-wrap gap-3">
            <Link href="/exporter" className="btn btn-primary">
              Start the demo
            </Link>
            <Link href={sample.href} className="btn btn-ghost">
              {sample.hasCert ? "See a sample certificate" : "Create a certificate first"}
            </Link>
          </div>
        </div>
      </Glass>

      <DemoRunnerSection />

      <Glass>
        <h2 className="mb-5 text-2xl font-bold sm:text-3xl">Attest, review, allocate, verify</h2>
        <ol className="flow-grid m-0 grid list-none gap-4 p-0 sm:grid-cols-2 lg:grid-cols-4">
          {FLOWS.map((f) => (
            <FlowCard key={f.n} f={f} />
          ))}
        </ol>
        {DEMO_MODE && <p className="hint mt-4">Each button switches to the right demo persona for you.</p>}
      </Glass>

      <Glass>
        <div className="flex flex-col gap-5">
          <div className="tile p-5 sm:p-6">
            <h2 className="mb-2 text-xl font-bold sm:text-2xl">Why a blockchain, not a database?</h2>
            <p className="max-w-3xl leading-relaxed">
              A Turkish mill, its EU importers, an accredited verifier and a regulator don&apos;t share an IT system or trust one operator. The allocation ledger has to be one none of them
              controls: the contract enforces allocated ≤ attested tonnes for everyone at once, and the record outlives any one company&apos;s server; CBAM records are kept for years.
            </p>
          </div>
          <p className="text-sm text-[var(--color-mute)]">
            The chain stores a 32-byte fingerprint of each report, never the report. The report stays encrypted; only the certificate link (the QR code) carries the key.
          </p>
          <TechnicalStatus />
        </div>
      </Glass>
    </div>
  );
}
