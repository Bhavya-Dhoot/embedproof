import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { CBAM_CAVEAT, CBAM_YEARS, cbamDefaultFor, cbamImpact, directIntensity } from "../src/lib/cbam";
import { CBAM_STEEL_DEFAULTS } from "../src/lib/cbamDefaults";

const fixtures = fileURLToPath(new URL("../../data/fixtures/", import.meta.url));
const csvPath = fixtures + "cbam_defaults_steel.csv";

function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((x) => x !== "")) rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  const [head, ...body] = rows;
  return body.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
}

describe.skipIf(!existsSync(csvPath))("checked-in CBAM defaults equal data/fixtures/cbam_defaults_steel.csv", () => {
  it("every CSV row is in the generated table with the same route, CN code and direct value, and vice versa", () => {
    const csv = parseCsv(readFileSync(csvPath, "utf8"));
    expect(CBAM_STEEL_DEFAULTS.length).toBe(csv.length);
    for (const r of csv) {
      const hit = CBAM_STEEL_DEFAULTS.find((x) => x.iso3 === r.iso3 && x.route === r.route);
      expect(hit, `${r.iso3} ${r.route}`).toBeDefined();
      expect(hit!.direct, `${r.iso3} ${r.route} direct`).toBe(Number(r.direct_tco2_per_t));
      expect(hit!.cn, `${r.iso3} ${r.route} cn`).toBe(r.cn_code);
    }
  });

  it("every country except the fallback row has an ISO-2 code (so installations resolve)", () => {
    for (const r of CBAM_STEEL_DEFAULTS) if (r.iso3 !== "OTHER") expect(r.iso2, r.iso3).toMatch(/^[A-Z]{2}$/);
  });
});

describe("default value lookup (direct emissions, before the mark-up)", () => {
  it("Turkiye: BF-BOF 2.428, EAF 2.310, DRI-EAF 2.310", () => {
    expect(cbamDefaultFor("TR", "BF-BOF")).toMatchObject({ direct: 2.428, cn: "7208", fallback: false });
    expect(cbamDefaultFor("TR", "EAF")).toMatchObject({ direct: 2.31, cn: "7214 20 00", fallback: false });
    expect(cbamDefaultFor("TR", "DRI-EAF")).toMatchObject({ direct: 2.31, cn: "7207 11 14", fallback: false });
  });
  it("falls back to Other Countries (4.049 / 3.996 / 3.996) and says so", () => {
    expect(cbamDefaultFor("ZZ", "BF-BOF")).toMatchObject({ direct: 4.049, fallback: true });
    expect(cbamDefaultFor("ZZ", "EAF")).toMatchObject({ direct: 3.996, fallback: true });
    expect(cbamDefaultFor("ZZ", "DRI-EAF")).toMatchObject({ direct: 3.996, fallback: true });
  });
  it("cement routes have no steel default", () => {
    expect(cbamDefaultFor("TR", "CEMENT-DRY")).toBeNull();
  });
});

describe("impact arithmetic with the honest EAF demo report (5000 t, 1200 t direct, SEE 0.535)", () => {
  const report = existsSync(fixtures + "../demo_reports/honest_eaf.json")
    ? (JSON.parse(readFileSync(fixtures + "../demo_reports/honest_eaf.json", "utf8")) as { directEmissionsTco2: number; productionTonnes: number; seeTco2PerT: number })
    : { directEmissionsTco2: 1200, productionTonnes: 5000, seeTco2PerT: 0.535 };
  const D = cbamDefaultFor("TR", "EAF")!.direct;
  const A = directIntensity(report)!;

  it("A is direct emissions over production, not the SEE", () => {
    expect(A).toBeCloseTo(0.24, 10);
    expect(A).not.toBeCloseTo(report.seeTco2PerT, 3);
  });

  it("2026 (+10%): T x D x 1.1, T x A, difference and percent", () => {
    const r = cbamImpact({ tonnes: 5000, defaultValue: D, attested: A, year: 2026 })!;
    expect(r.withDefault).toBeCloseTo(12705, 6); // 5000 x 2.31 x 1.1
    expect(r.withCertificate).toBeCloseTo(1200, 6); // 5000 x 0.24
    expect(r.difference).toBeCloseTo(11505, 6);
    expect(r.differencePct).toBeCloseTo((11505 / 12705) * 100, 8);
    expect(r.euro).toBeUndefined(); // never a euro figure without a typed price
  });

  it("2027 (+20%) and 2028 (+30%)", () => {
    expect(cbamImpact({ tonnes: 5000, defaultValue: D, attested: A, year: 2027 })!.withDefault).toBeCloseTo(13860, 6);
    expect(cbamImpact({ tonnes: 5000, defaultValue: D, attested: A, year: 2028 })!.withDefault).toBeCloseTo(15015, 6);
    expect(CBAM_YEARS.map((y) => y.markup)).toEqual([0.1, 0.2, 0.3]);
  });

  it("the euro line exists only for a typed price: difference x price", () => {
    const r = cbamImpact({ tonnes: 5000, defaultValue: D, attested: A, year: 2026, price: 100 })!;
    expect(r.euro).toBeCloseTo(1_150_500, 4);
    expect(cbamImpact({ tonnes: 5000, defaultValue: D, attested: A, year: 2026, price: Number.NaN })!.euro).toBeUndefined();
    expect(cbamImpact({ tonnes: 5000, defaultValue: D, attested: A, year: 2026, price: undefined })!.euro).toBeUndefined();
  });

  it("without the key the on-chain SEE (which includes indirect) understates the gap", () => {
    const withSee = cbamImpact({ tonnes: 5000, defaultValue: D, attested: report.seeTco2PerT, year: 2026 })!;
    const withDirect = cbamImpact({ tonnes: 5000, defaultValue: D, attested: A, year: 2026 })!;
    expect(withSee.withCertificate).toBeCloseTo(2675, 6);
    expect(withSee.difference).toBeLessThan(withDirect.difference);
  });

  it("a different tonnage scales linearly; a negative or non-numeric input yields no result", () => {
    const half = cbamImpact({ tonnes: 2500, defaultValue: D, attested: A, year: 2026 })!;
    expect(half.difference).toBeCloseTo(11505 / 2, 6);
    expect(cbamImpact({ tonnes: -1, defaultValue: D, attested: A, year: 2026 })).toBeNull();
    expect(cbamImpact({ tonnes: Number.NaN, defaultValue: D, attested: A, year: 2026 })).toBeNull();
  });
});

describe("caveat text", () => {
  it("is the required wording, with no em or en dash", () => {
    expect(CBAM_CAVEAT.startsWith("Simplified illustration, not a CBAM declaration.")).toBe(true);
    expect(CBAM_CAVEAT).toContain("(EU) 2025/2621 as corrected by (EU) 2026/1740");
    expect(CBAM_CAVEAT).toContain("BF-BOF 7208, EAF 7214 20 00, DRI-EAF 7207 11 14.");
    expect(/[—–]/.test(CBAM_CAVEAT)).toBe(false);
  });
});
