import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import type { Roles } from "../src/lib/chainReads";
import { holdsRole, primaryRole, roleMismatchMessage } from "../src/lib/roles";
import { LAST_CERT_KEY, readLastCert, rememberCert, resolveSampleCert, sampleTarget } from "../src/lib/sampleCert";
import { NO_HISTORY_NOTE, defaultComparison, flagText, plainSummary, wordCount } from "../src/lib/summary";
import { describeRunError, explorerTx, stepOutcome } from "../src/lib/demoRun";
import { ApiError } from "../src/lib/attestor";
import { FALLBACK_DEMO_REPORTS } from "../src/lib/demoMeta";

const roles = (over: Partial<Roles> = {}): Roles => ({ admin: false, verifier: false, regulator: false, operatorOf: [], known: true, ...over });

describe("role views (PRD FR-F1)", () => {
  it("names the role the account is using and the role the page is for", () => {
    expect(roleMismatchMessage(roles({ verifier: true }), "operator")).toBe("You're using Verifier; this page is for Exporter.");
    expect(roleMismatchMessage(roles({ operatorOf: ["TR-EAF-0001"] }), "verifier")).toBe("You're using Exporter; this page is for Verifier.");
    expect(roleMismatchMessage(roles(), "operator")).toBe("You're using Importer; this page is for Exporter.");
    expect(roleMismatchMessage(roles({ admin: true }), "importer")).toBe("You're using Admin; this page is for Importer.");
  });

  it("no message when the account fits, or when roles have not been read yet", () => {
    expect(roleMismatchMessage(roles({ operatorOf: ["x"] }), "operator")).toBeNull();
    expect(roleMismatchMessage(roles(), "importer")).toBeNull();
    expect(roleMismatchMessage(roles({ known: false }), "operator")).toBeNull();
  });

  it("importer is the default role; admin outranks the rest", () => {
    expect(primaryRole(roles({ admin: true, verifier: true, operatorOf: ["x"] }))).toBe("admin");
    expect(holdsRole(roles(), "importer")).toBe(true);
    expect(holdsRole(roles({ regulator: true }), "importer")).toBe(false);
    expect(holdsRole(roles({ regulator: true }), "regulator")).toBe(true);
  });
});

describe("sample certificate link", () => {
  const store = new Map<string, string>();
  const g = globalThis as unknown as Record<string, unknown>;
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };
  g.window = { dispatchEvent: () => true };
  afterEach(() => store.clear());

  it("prefers NEXT_PUBLIC_SAMPLE_CERT_URL, then this browser's last certificate, else nothing", () => {
    expect(resolveSampleCert("https://demo.example/verify/3#k=abc", "/verify/9#k=zzz")).toBe("https://demo.example/verify/3#k=abc");
    expect(resolveSampleCert("", "/verify/9#k=zzz")).toBe("/verify/9#k=zzz");
    expect(resolveSampleCert("", null)).toBeNull();
  });

  it("with no certificate the entry points go to /exporter with the hint", () => {
    expect(sampleTarget(null)).toEqual({ href: "/exporter", hasCert: false, hint: "Create a certificate first" });
    expect(sampleTarget("/verify/2#k=abc")).toEqual({ href: "/verify/2#k=abc", hasCert: true, hint: null });
  });

  it("remembers a certificate path and reads it back, rejecting anything that is not a /verify/<id> path", () => {
    rememberCert("/verify/4#k=AbC_-123");
    expect(readLastCert()).toBe("/verify/4#k=AbC_-123");
    expect(JSON.parse(store.get(LAST_CERT_KEY)!).path).toBe("/verify/4#k=AbC_-123");
    rememberCert("https://evil.example/verify/5");
    expect(readLastCert()).toBe("/verify/4#k=AbC_-123");
    store.set(LAST_CERT_KEY, JSON.stringify({ path: "//evil.example" }));
    expect(readLastCert()).toBeNull();
    store.set(LAST_CERT_KEY, "not json");
    expect(readLastCert()).toBeNull();
  });

  it("survives blocked storage (every access throws)", () => {
    g.localStorage = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(() => rememberCert("/verify/1#k=abc")).not.toThrow();
    expect(readLastCert()).toBeNull();
  });
});

describe("plain summary, EC default comparison, flags", () => {
  const green = { band: "green" as const, expectedLow: 0.4323, expectedHigh: 0.9633, cbamDefault: 2.31 };
  it("stays within 25 words for every band and position", () => {
    for (const band of ["green", "amber", "red"] as const) {
      for (const see of [0.2, 0.6, 1.5]) {
        const t = plainSummary({ ...green, band }, see);
        expect(wordCount(t), t).toBeLessThanOrEqual(25);
        expect(/[—–]/.test(t)).toBe(false);
      }
    }
  });
  it("says where the declared value sits and what happens next", () => {
    expect(plainSummary(green, 0.535)).toBe("Declared 0.535 tCO₂/t is inside the 0.43 to 0.96 range expected for this plant.");
    expect(plainSummary({ ...green, band: "red" }, 0.2)).toContain("below the 0.43 to 0.96 range");
    expect(plainSummary({ ...green, band: "red" }, 0.2)).toContain("A human verifier must review it.");
    expect(plainSummary({ ...green, band: "amber" }, 1.2)).toContain("above the");
    expect(plainSummary({ ...green, band: "amber" }, 1.2)).toContain("attested, with a visible flag");
  });
  it("shows the EC default as a value, never as a risk penalty", () => {
    const t = defaultComparison(0.535, 2.31)!;
    expect(t).toContain("77% below the EC default value of 2.31");
    expect(/risk|scrutiny|\+20/i.test(t)).toBe(false);
    expect(defaultComparison(3, 2.31)).toContain("30% above");
    expect(defaultComparison(1, 0)).toBeNull();
  });
  it("renders the flag's message, a known fallback, or the code; and the no-history note", () => {
    expect(flagText({ code: "GRID_FACTOR_BELOW_EMBER", message: "Grid factor 0.20 is below Ember's 0.476" })).toBe("Grid factor 0.20 is below Ember's 0.476");
    expect(flagText({ code: "GRID_FACTOR_BELOW_EMBER" })).toContain("below the Ember figure");
    expect(flagText("ELECTRICITY_OUTSIDE_ROUTE_NORM")).toContain("outside the usual range");
    expect(flagText({ code: "NEW_CODE" })).toBe("NEW_CODE");
    expect(NO_HISTORY_NOTE).toBe("No plant history: wider interval, lower detection power");
  });
});

describe("hosted demo runner helpers", () => {
  it("ok=false with expected=true reads as an expected rejection, not an error", () => {
    expect(stepOutcome({ ok: true, expected: true })).toEqual({ tone: "green", text: "Done" });
    expect(stepOutcome({ ok: false, expected: true })).toEqual({ tone: "amber", text: "Rejected as expected" });
    expect(stepOutcome({ ok: false, expected: false })).toEqual({ tone: "red", text: "Unexpected result" });
    expect(stepOutcome({ ok: true, expected: false }).tone).toBe("red");
  });
  it("explorer link on Base Sepolia only; the local chain shows the hash", () => {
    expect(explorerTx(84532, "0xabc")).toBe("https://sepolia.basescan.org/tx/0xabc");
    expect(explorerTx(31337, "0xabc")).toBeNull();
  });
  it("404, 409 and 429 (with Retry-After) have their own messages", () => {
    expect(describeRunError(new ApiError(404, "nope"))).toContain("not available");
    expect(describeRunError(new ApiError(409, "busy"))).toContain("already running");
    expect(describeRunError(new ApiError(429, "slow down", undefined, 42))).toContain("in 42 seconds");
    expect(describeRunError(new ApiError(429, "slow down"))).toContain("in a minute");
    expect(describeRunError(new ApiError(500, "boom"))).toBe("boom");
  });
});

describe("built-in demo reports match the shipped data/demo_reports", () => {
  const dir = fileURLToPath(new URL("../../data/demo_reports/", import.meta.url));
  it.skipIf(!existsSync(dir + "honest_eaf.json"))("honest_eaf and implausible_bof", () => {
    expect(FALLBACK_DEMO_REPORTS.honest_eaf).toEqual(JSON.parse(readFileSync(dir + "honest_eaf.json", "utf8")));
    expect(FALLBACK_DEMO_REPORTS.implausible_bof).toEqual(JSON.parse(readFileSync(dir + "implausible_bof.json", "utf8")));
  });
});
