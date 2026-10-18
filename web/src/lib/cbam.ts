import { CBAM_STEEL_DEFAULTS, type CbamDefaultRow } from "./cbamDefaults";
import type { RouteName } from "./report";

/** Mark-up on the default value by import year (Annex I introduction): 10% in 2026, 20% in 2027, 30% from 2028. */
export type CbamYear = 2026 | 2027 | 2028;
export const CBAM_YEARS: { year: CbamYear; markup: number; label: string }[] = [
  { year: 2026, markup: 0.1, label: "2026 (+10%)" },
  { year: 2027, markup: 0.2, label: "2027 (+20%)" },
  { year: 2028, markup: 0.3, label: "2028 and later (+30%)" },
];
export const markupFor = (year: CbamYear): number => CBAM_YEARS.find((y) => y.year === year)!.markup;

/** Shown verbatim under the impact panel. */
export const CBAM_CAVEAT =
  "Simplified illustration, not a CBAM declaration. Ignores the benchmark-based free-allocation adjustment and any carbon price paid in Türkiye. Default values: EC informational workbook v2 (6 Aug 2026) for Annex I to Implementing Regulation (EU) 2025/2621 as corrected by (EU) 2026/1740, printed before the mark-up; mark-up 10% / 20% / 30% for 2026 / 2027 / 2028+ per Annex I's introduction. One representative CN code per route: BF-BOF 7208, EAF 7214 20 00, DRI-EAF 7207 11 14.";

export interface CbamDefault {
  /** direct emissions default value D, tCO2 per tonne, BEFORE the mark-up */
  direct: number;
  cn: string;
  /** country the value belongs to: the installation's, or "Other countries and territories" as the workbook's fallback */
  country: string;
  fallback: boolean;
}

/** Default value for an installation's country (ISO-2) and route; null for routes the steel table does not cover (cement). */
export function cbamDefaultFor(iso2: string, route: RouteName): CbamDefault | null {
  if (route !== "BF-BOF" && route !== "EAF" && route !== "DRI-EAF") return null;
  const find = (pred: (r: CbamDefaultRow) => boolean) => CBAM_STEEL_DEFAULTS.find((r) => r.route === route && pred(r));
  const own = find((r) => r.iso2 !== "" && r.iso2.toUpperCase() === iso2.toUpperCase());
  const row = own ?? find((r) => r.iso3 === "OTHER");
  return row ? { direct: row.direct, cn: row.cn, country: row.country, fallback: !own } : null;
}

export interface CbamInput {
  /** tonnes T */
  tonnes: number;
  /** default value D (tCO2/t, before mark-up) */
  defaultValue: number;
  /** attested intensity A (tCO2/t) */
  attested: number;
  year: CbamYear;
  /** carbon price in EUR per tCO2e; leave undefined or NaN when the user has not entered one */
  price?: number;
}

export interface CbamResult {
  markup: number;
  /** T x D x (1 + m) */
  withDefault: number;
  /** T x A */
  withCertificate: number;
  /** withDefault - withCertificate */
  difference: number;
  /** difference / withDefault x 100 */
  differencePct: number;
  /** difference x price; only when a price was entered */
  euro?: number;
}

export function cbamImpact(i: CbamInput): CbamResult | null {
  if (![i.tonnes, i.defaultValue, i.attested].every((x) => Number.isFinite(x) && x >= 0)) return null;
  const markup = markupFor(i.year);
  const withDefault = i.tonnes * i.defaultValue * (1 + markup);
  const withCertificate = i.tonnes * i.attested;
  const difference = withDefault - withCertificate;
  const r: CbamResult = { markup, withDefault, withCertificate, difference, differencePct: withDefault > 0 ? (difference / withDefault) * 100 : 0 };
  if (i.price !== undefined && Number.isFinite(i.price) && i.price >= 0) r.euro = difference * i.price;
  return r;
}

/** Direct emissions per tonne from a (decrypted) report: CBAM counts only direct emissions for iron and steel. */
export function directIntensity(report: { directEmissionsTco2?: unknown; productionTonnes?: unknown } | null | undefined): number | null {
  const d = report?.directEmissionsTco2;
  const p = report?.productionTonnes;
  return typeof d === "number" && typeof p === "number" && p > 0 && Number.isFinite(d) ? d / p : null;
}
