"use client";

import Link from "next/link";
import type { Hex } from "viem";
import { useState, type ComponentProps, type ElementType, type ReactNode } from "react";
import { explorerTxUrl, CHAIN_LABEL } from "@/lib/env";
import { useDeployment, useRoles } from "@/lib/hooks";
import { roleMismatchMessage, type ViewRole } from "@/lib/roles";
import { useSigner } from "@/lib/signer";
import { bandFor, bytes32ToPrintable, shortHex, type Band } from "@/lib/report";
import type { PersonaKey } from "@/lib/demoAccounts";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

/** Frosted panel. Keep the number of these on screen moderate (backdrop-filter is the costly part). */
export function Glass({
  children,
  className,
  pad = true,
  delay = 0,
  as = "section",
  ...rest
}: { children: ReactNode; className?: string; pad?: boolean; delay?: number; as?: "section" | "div" | "article" | "header" } & Omit<ComponentProps<"section">, "children" | "ref">) {
  const Tag = as as ElementType;
  return (
    <Tag className={cx("glass", className)} style={{ ["--sheen-delay" as string]: `${-delay}s` }} {...rest}>
      <span className="sheen" aria-hidden />
      <div className={cx("relative z-[1]", pad && "p-5 sm:p-7")}>{children}</div>
    </Tag>
  );
}

export function Tile({ children, className, ...rest }: ComponentProps<"div">) {
  return (
    <div className={cx("tile p-4", className)} {...rest}>
      {children}
    </div>
  );
}

export function Chip({ tone = "neutral", children, className }: { tone?: "green" | "amber" | "red" | "neutral"; children: ReactNode; className?: string }) {
  return <span className={cx("chip", `chip-${tone}`, className)}>{children}</span>;
}

const bandTone: Record<Band, "green" | "amber" | "red"> = { green: "green", amber: "amber", red: "red" };
export const bandLabel: Record<Band, string> = { green: "Green · within expected range", amber: "Amber · flagged", red: "Red · outside expected range" };

export function BandChip({ band, risk }: { band?: Band; risk?: number }) {
  const b = band ?? (risk !== undefined ? bandFor(risk) : "green");
  return (
    <Chip tone={bandTone[b]}>
      <span aria-hidden>●</span> {bandLabel[b]}
    </Chip>
  );
}

export function StatusChip({ status }: { status: string }) {
  const tone = status === "Attested" ? "green" : status === "PendingVerifier" ? "amber" : status === "Rejected" ? "red" : "neutral";
  const label = status === "PendingVerifier" ? "Pending verifier" : status;
  return <Chip tone={tone}>{label}</Chip>;
}

export function PageHeader({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: ReactNode }) {
  return (
    <header className="mb-6 sm:mb-8">
      {eyebrow && <p className="mb-2 text-xs font-bold uppercase tracking-[0.16em] text-[var(--color-teal-text)]">{eyebrow}</p>}
      <h1 className="text-3xl font-bold leading-tight tracking-tight sm:text-5xl">{title}</h1>
      {children && <p className="mt-3 max-w-3xl text-base text-[var(--color-mute)] sm:text-lg">{children}</p>}
    </header>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-[var(--color-mute)]" role="status">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" aria-hidden />
      {label ?? "Loading…"}
    </span>
  );
}

export function Notice({ tone = "neutral", title, children }: { tone?: "neutral" | "amber" | "red" | "green"; title?: string; children?: ReactNode }) {
  const border = { neutral: "border-white/25", amber: "border-[var(--color-amber)]", red: "border-[var(--color-rose)]", green: "border-[var(--color-teal)]" }[tone];
  return (
    <div role={tone === "red" ? "alert" : "status"} className={cx("tile border-l-4 p-4", border)}>
      {title && <p className="font-bold">{title}</p>}
      {children && <div className="text-sm text-[var(--color-mute)]">{children}</div>}
    </div>
  );
}

/** Copy-friendly monospace value with a short form. */
export function Mono({ value, short = true, className }: { value: string; short?: boolean; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={cx("mono inline-flex max-w-full items-center gap-2 rounded-lg bg-black/25 px-2 py-1 text-left hover:bg-black/40", className)}
      title={`${value} (click to copy)`}
      onClick={() => {
        navigator.clipboard?.writeText(value).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          },
          () => undefined,
        );
      }}
    >
      <span className="break-all">{short ? shortHex(value, 8, 6) : value}</span>
      <span className="text-[0.65rem] uppercase tracking-wider text-white/70" aria-live="polite">
        {copied ? "copied" : "copy"}
      </span>
    </button>
  );
}

/** A bytes32 consignment id shown as the ASCII string it encodes; the raw hex stays one click away (copy button). */
export function ConsignmentId({ id, className }: { id: Hex; className?: string }) {
  const [copied, setCopied] = useState(false);
  const text = bytes32ToPrintable(id);
  return (
    <span className={cx("inline-flex max-w-full flex-wrap items-center gap-x-2 gap-y-1", className)}>
      <span className={cx("break-all font-bold", !text && "mono")} title={`bytes32 ${id}`}>
        {text ?? shortHex(id, 8, 6)}
      </span>
      <button
        type="button"
        className="rounded-md border border-white/30 bg-white/10 px-1.5 py-0.5 text-[0.65rem] font-bold uppercase tracking-wider text-white/85 hover:bg-white/20"
        aria-label="Copy the consignment id as bytes32 hex"
        onClick={() => {
          navigator.clipboard?.writeText(id).then(
            () => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            },
            () => undefined,
          );
        }}
      >
        <span aria-live="polite">{copied ? "copied" : "copy hex"}</span>
      </button>
    </span>
  );
}

/** FR-F6: explorer link on every transaction (Base Sepolia); hash + "local Anvil" otherwise. */
export function TxLink({ hash }: { hash: string }) {
  const url = explorerTxUrl(hash);
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Mono value={hash} />
      {url ? (
        <a className="text-sm font-bold underline decoration-[var(--color-teal)] underline-offset-4" href={url} target="_blank" rel="noreferrer">
          View on explorer ↗
        </a>
      ) : (
        <span className="text-xs text-white/75">{CHAIN_LABEL} (no explorer)</span>
      )}
    </span>
  );
}

export function NeedsDeployment({ children }: { children: ReactNode }) {
  const dep = useDeployment();
  if (!dep) {
    return (
      <Glass>
        <div className="flex flex-col gap-3">
          <Chip tone="amber">Contracts not deployed</Chip>
          <h2 className="text-2xl font-bold">Contracts not deployed: run make demo</h2>
          <p className="max-w-2xl text-[var(--color-mute)]">
            No <code className="mono">deployments/{`<chainId>`}.json</code> was found when this app was built. Start Anvil, deploy the contracts and the attestor with{" "}
            <code className="mono">make demo</code>, then restart <code className="mono">pnpm dev</code> (it re-syncs the deployment files on start).
          </p>
        </div>
      </Glass>
    );
  }
  return <>{children}</>;
}

const ROLE_PERSONAS: Record<ViewRole, PersonaKey[]> = {
  operator: ["exporterA", "exporterB"],
  verifier: ["verifier"],
  importer: ["importerA", "importerB"],
  regulator: ["regulator"],
  admin: ["admin"],
};

/**
 * Role banner (PRD FR-F1). No account chosen: ask for one. Account without the role this page needs:
 * "You're using <role>; this page is for <role>." and the page disables its write actions (useRoleGate).
 * Demo mode offers one-click persona switches; a wallet user switches account in the wallet.
 */
export function ActAs({ role, keys, reason }: { role?: ViewRole; keys?: PersonaKey[]; reason?: string }) {
  const s = useSigner();
  const roles = useRoles();
  if (!s.ready) return null;
  const personaKeys = keys ?? (role ? ROLE_PERSONAS[role] : []);
  const switchers = s.mode === "demo" ? s.personas.filter((p) => personaKeys.includes(p.key)) : [];

  if (!s.address) {
    return (
      <Notice tone="amber" title={s.mode === "wallet" ? "Connect a wallet" : "Choose who you are acting as"}>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span>{reason ?? (s.mode === "wallet" ? "This view needs a connected wallet." : "Demo mode: pick a persona to use this page.")}</span>
          {s.mode === "wallet" && (
            <button className="btn btn-primary btn-sm" onClick={s.connect}>
              Connect wallet
            </button>
          )}
          {switchers.map((p) => (
            <button key={p.key} className="btn btn-ghost btn-sm" onClick={() => s.selectPersona(p.key)}>
              Act as {p.label}
            </button>
          ))}
        </div>
      </Notice>
    );
  }

  const mismatch = role ? roleMismatchMessage(roles, role) : null;
  if (!mismatch) return null;
  return (
    <Notice tone="amber" title={mismatch}>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span>Read-only here: the write actions on this page are disabled for this account.</span>
        {switchers.map((p) => (
          <button key={p.key} className="btn btn-ghost btn-sm" onClick={() => s.selectPersona(p.key)}>
            Act as {p.label}
          </button>
        ))}
      </div>
    </Notice>
  );
}

export function LinkButton({ href, children, variant = "ghost" }: { href: string; children: ReactNode; variant?: "primary" | "ghost" }) {
  return (
    <Link href={href} className={cx("btn", variant === "primary" ? "btn-primary" : "btn-ghost")}>
      {children}
    </Link>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div>
      <label className="lbl" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint && <p className="hint mt-1">{hint}</p>}
    </div>
  );
}
