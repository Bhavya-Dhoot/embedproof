"use client";

import { useSyncExternalStore } from "react";
import { SAMPLE_CERT_URL } from "./env";

/** The last certificate created in this browser (exporter attest or demo runner): a path like /verify/3#k=<key>. */
export const LAST_CERT_KEY = "embedproof:lastCert";
const EVENT = "embedproof:cert";
const PATH_RE = /^\/verify\/\d+(#k=[A-Za-z0-9_-]+)?$/;

export function rememberCert(path: string): void {
  if (!PATH_RE.test(path)) return;
  try {
    localStorage.setItem(LAST_CERT_KEY, JSON.stringify({ path, at: Date.now() }));
    window.dispatchEvent(new Event(EVENT));
  } catch {
    /* private mode or blocked storage: the sample link simply falls back to the exporter */
  }
}

export function readLastCert(): string | null {
  try {
    const raw = localStorage.getItem(LAST_CERT_KEY);
    const path = raw ? (JSON.parse(raw) as { path?: unknown }).path : null;
    return typeof path === "string" && PATH_RE.test(path) ? path : null;
  } catch {
    return null;
  }
}

/** Preference order: NEXT_PUBLIC_SAMPLE_CERT_URL, then this browser's last certificate, else null. */
export function resolveSampleCert(envUrl: string, last: string | null): string | null {
  return envUrl || last || null;
}

function subscribe(cb: () => void) {
  window.addEventListener("storage", cb);
  window.addEventListener(EVENT, cb);
  return () => {
    window.removeEventListener("storage", cb);
    window.removeEventListener(EVENT, cb);
  };
}

/** href of the certificate the "Verify a certificate" buttons open, or null when none exists yet (then send people to /exporter). */
export function useSampleCert(): string | null {
  const last = useSyncExternalStore(subscribe, readLastCert, () => null);
  return resolveSampleCert(SAMPLE_CERT_URL, last);
}

/** Where a "Verify a certificate" entry point goes, and the hint to show when it cannot go to a certificate. */
export function sampleTarget(href: string | null): { href: string; hasCert: boolean; hint: string | null } {
  return href ? { href, hasCert: true, hint: null } : { href: "/exporter", hasCert: false, hint: "Create a certificate first" };
}
