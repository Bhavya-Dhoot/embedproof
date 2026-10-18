"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { CHAIN_LABEL, DEMO_MODE } from "@/lib/env";
import { useChainStatus, useDeployment, useRoles } from "@/lib/hooks";
import { shortAddr } from "@/lib/report";
import { useSigner } from "@/lib/signer";
import type { PersonaKey } from "@/lib/demoAccounts";
import { cx } from "./ui";

const NAV: { href: string; label: string; role?: "operator" | "verifier" | "importer" | "regulator" | "admin" }[] = [
  { href: "/exporter", label: "Exporter", role: "operator" },
  { href: "/verifier", label: "Verifier", role: "verifier" },
  { href: "/importer", label: "Importer", role: "importer" },
  { href: "/regulator", label: "Regulator", role: "regulator" },
  { href: "/admin", label: "Admin", role: "admin" },
  { href: "/verify/1", label: "Public verify" },
];

export function PersonaSwitcher({ className }: { className?: string }) {
  const s = useSigner();
  if (!s.ready) return <div className={cx("h-11 w-44 rounded-full bg-white/10", className)} aria-hidden />;

  if (s.mode === "wallet") {
    return s.address ? (
      <button className={cx("btn btn-ghost btn-sm", className)} onClick={s.disconnect} title="Disconnect wallet">
        <span className="mono !text-xs">{shortAddr(s.address)}</span>
        <span className="text-xs text-white/75">disconnect</span>
      </button>
    ) : (
      <button className={cx("btn btn-primary btn-sm", className)} onClick={s.connect}>
        Connect wallet
      </button>
    );
  }

  return (
    <label className={cx("flex items-center gap-2", className)}>
      <span className="sr-only">Acting as</span>
      <select
        className="inp !min-h-[40px] w-full !rounded-full !py-1.5 !pr-8 text-sm font-bold sm:!w-auto"
        value={s.personaKey ?? ""}
        onChange={(e) => e.target.value && s.selectPersona(e.target.value as PersonaKey)}
        aria-label="Acting as (demo persona)"
      >
        <option value="" disabled>
          Choose persona…
        </option>
        {s.personas.map((p) => (
          <option key={p.key} value={p.key}>
            {p.label} · {shortAddr(p.address)}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ChainPill() {
  const dep = useDeployment();
  const st = useChainStatus();
  const up = st.data !== undefined;
  return (
    <span className="chip chip-neutral !normal-case !tracking-normal" title={dep ? `registry ${dep.registry}` : "contracts not deployed"}>
      <span className={cx("inline-block h-2 w-2 rounded-full", up ? "bg-[var(--color-teal)]" : "bg-[var(--color-rose)]")} aria-hidden />
      {CHAIN_LABEL}
      {up && <span className="num text-white/85">#{st.data.block.toString()}</span>}
      {st.data?.paused && <span className="font-bold text-[var(--color-amber-text)]">PAUSED</span>}
    </span>
  );
}

/**
 * Navigation: inline links from 1280 px up; below that a Menu button opens a link grid inside the same glass bar.
 * Nothing scrolls sideways at any width, so no scrollbar ever shows under the links.
 */
export function Header() {
  const path = usePathname();
  const roles = useRoles();
  const { address, ready } = useSigner();
  const [open, setOpen] = useState(false);

  // close the menu on navigation and on Escape
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const mine = (r?: (typeof NAV)[number]["role"]) =>
    !!address &&
    ((r === "operator" && roles.operatorOf.length > 0) ||
      (r === "verifier" && roles.verifier) ||
      (r === "regulator" && roles.regulator) ||
      (r === "admin" && roles.admin) ||
      (r === "importer" && roles.known && !roles.admin && !roles.verifier && !roles.regulator && roles.operatorOf.length === 0));

  const isActive = (href: string) => path === href || (href !== "/" && path.startsWith(href.split("/").slice(0, 2).join("/")));

  const links = (vertical: boolean) =>
    NAV.map((n) => {
      const active = isActive(n.href);
      return (
        <Link
          key={n.href}
          href={n.href}
          aria-current={active ? "page" : undefined}
          className={cx(
            "relative whitespace-nowrap rounded-full px-3 py-2 text-sm font-bold transition-colors",
            vertical && "bg-white/5 px-4 py-3 text-base",
            active ? "bg-white/20 text-white" : "text-white/85 hover:bg-white/15 hover:text-white",
          )}
        >
          {n.label}
          {ready && mine(n.role) && (
            <span className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-teal)] align-middle" title="your role" aria-label="your role" />
          )}
        </Link>
      );
    });

  return (
    <header className="z-40 px-3 pt-3 sm:px-6 min-[1280px]:sticky min-[1280px]:top-0">
      <div className="glass !rounded-[28px] min-[1280px]:!rounded-full">
        <span className="sheen" aria-hidden />
        <div className="relative z-[1] px-4 py-2.5 sm:px-6">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <Link href="/" className="flex items-center gap-2.5 rounded-full" aria-label="EmbedProof home">
              <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden>
                <defs>
                  <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#3B2BFF" />
                    <stop offset="1" stopColor="#00C2A8" />
                  </linearGradient>
                </defs>
                <rect x="2" y="2" width="28" height="28" rx="9" fill="url(#lg)" />
                <path d="M9 16.5l4.6 4.6L23 11.2" fill="none" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="text-lg font-bold tracking-tight">EmbedProof</span>
            </Link>

            <nav aria-label="Primary" className="ml-2 hidden flex-1 gap-1 min-[1280px]:flex">
              {links(false)}
            </nav>

            <span className="ml-auto hidden md:inline-flex">
              <ChainPill />
            </span>
            <PersonaSwitcher className="order-last w-full sm:order-none sm:w-auto" />
            <button
              type="button"
              className="btn btn-ghost btn-sm ml-auto sm:ml-0 min-[1280px]:!hidden"
              aria-expanded={open}
              aria-controls="site-menu"
              onClick={() => setOpen((o) => !o)}
            >
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
                {open ? (
                  <path d="M4 4l10 10M14 4L4 14" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
                ) : (
                  <path d="M3 5h12M3 9h12M3 13h12" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
                )}
              </svg>
              Menu
            </button>
          </div>

          {open && (
            <nav id="site-menu" aria-label="Primary" className="mt-3 grid gap-2 sm:grid-cols-3 min-[1280px]:hidden">
              {links(true)}
            </nav>
          )}

          {DEMO_MODE && (
            <p className="mt-2 w-full text-center text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[var(--color-amber-text)]">
              Demo mode · public Anvil test keys · local chain only
            </p>
          )}
        </div>
      </div>
    </header>
  );
}
