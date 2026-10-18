import { bytesToHex, hexToBytes, keccak256, stringToBytes, type Hex } from "viem";
import canonicalize from "canonicalize";

export const ROUTES = ["BF-BOF", "EAF", "DRI-EAF", "CEMENT-DRY", "CEMENT-WET"] as const;
export type RouteName = (typeof ROUTES)[number];
export const SECTORS = ["STEEL", "CEMENT"] as const;
export const STATUS = ["None", "PendingVerifier", "Attested", "Rejected"] as const;
export type StatusName = (typeof STATUS)[number];
export type Band = "green" | "amber" | "red";

/** docs/INTERFACES.md section 4 */
export interface Report {
  schema: "embedproof.report.v1";
  installationId: string;
  period: string; // YYYY-MM
  route: RouteName;
  cnCode: string;
  productionTonnes: number;
  directEmissionsTco2: number;
  electricityMwh: number;
  gridFactorTco2PerMwh: number;
  indirectEmissionsTco2: number;
  seeTco2PerT: number;
}

export interface ShapItem {
  feature: string;
  label: string;
  value: string | number;
  contribution: number;
}

/** docs/INTERFACES.md section 6 */
export interface ScoreResult {
  riskScore: number;
  band: Band;
  q05: number;
  q50: number;
  q95: number;
  expectedLow: number;
  expectedHigh: number;
  seeDeclared: number;
  cbamDefault: number;
  belowHalfDefault: boolean;
  modelVersionHash: Hex;
  shap: ShapItem[];
  explanation: string;
  latencyMs?: number;
}

export interface AttestResult {
  batchId: number | string;
  txHash: Hex;
  status: "Attested" | "PendingVerifier";
  riskScore: number;
  band: Band;
  cid: string;
  commitment: Hex;
  pqSigHash: Hex;
  ecdsaSignature: Hex;
  keyB64url: string;
  verifyPath: string;
  score: ScoreResult;
  gasUsed?: number;
}

export function bandFor(riskScore: number): Band {
  return riskScore <= 30 ? "green" : riskScore <= 60 ? "amber" : "red";
}

export function routeEnumToName(n: number): RouteName {
  return ROUTES[n] ?? ROUTES[0];
}

/** ASCII id -> bytes32 right-padded with zeros (matches the contract's installation ids). */
export function stringToBytes32(s: string): Hex {
  const bytes = stringToBytes(s);
  if (bytes.length === 0 || bytes.length > 32) throw new Error("id must be 1-32 bytes");
  if (bytes.length !== s.length) throw new Error("id must be plain ASCII");
  const out = new Uint8Array(32);
  out.set(bytes);
  return bytesToHex(out);
}

export function bytes32ToString(h: Hex): string {
  const b = hexToBytes(h);
  let end = b.length;
  while (end > 0 && b[end - 1] === 0) end--;
  return new TextDecoder().decode(b.slice(0, end));
}

/** Printable ASCII only -> string, else null (consignment ids may be arbitrary hashes). */
export function bytes32ToPrintable(h: Hex): string | null {
  const s = bytes32ToString(h);
  return s.length > 0 && /^[\x20-\x7E]+$/.test(s) ? s : null;
}

export function countryToBytes2(cc: string): Hex {
  if (!/^[A-Za-z]{2}$/.test(cc)) throw new Error("country must be 2 letters");
  return bytesToHex(stringToBytes(cc.toUpperCase()));
}

export function bytes2ToCountry(h: Hex): string {
  return new TextDecoder().decode(hexToBytes(h));
}

export function periodToUint32(p: string): number {
  const m = /^(\d{4})-(\d{2})$/.exec(p);
  if (!m) throw new Error("period must be YYYY-MM");
  return Number(m[1]) * 100 + Number(m[2]);
}

export function uint32ToPeriod(n: number): string {
  const s = String(n);
  return `${s.slice(0, 4)}-${s.slice(4)}`;
}

const round = (x: number, dp: number) => Math.round(x * 10 ** dp) / 10 ** dp;
export const computeIndirect = (electricityMwh: number, gridFactor: number) => round(electricityMwh * gridFactor, 3);
export const computeSee = (direct: number, indirect: number, production: number) =>
  production > 0 ? round((direct + indirect) / production, 4) : 0;
/** tCO2/t -> on-chain uint (x1e4, exact decimal half-up; see decimal.ts) */
export { seeToUint } from "./decimal";
export const uintToSee = (n: number | bigint) => Number(n) / 1e4;
export const formatSee = (n: number | bigint) => uintToSee(n).toFixed(4);

/** keccak256(JCS(report)) - what the exporter signs (EIP-191 over the raw 32 bytes). */
export function reportHash(report: Report): Hex {
  const c = canonicalize(report);
  if (c === undefined) throw new Error("report is not canonicalizable");
  return keccak256(stringToBytes(c));
}

/** Server-provided prose (model explanations, stored reasons, API details) with em and en dashes turned into plain punctuation. */
export function plain(s: string): string {
  return s.replace(/(\d)\s*\u2013\s*(\d)/g, "$1 to $2").replace(/\s*[\u2014\u2013]\s*/g, ", ");
}

export function shortHex(h: string, head = 6, tail = 4): string {
  return h.length <= head + tail + 2 ? h : `${h.slice(0, head + 2)}…${h.slice(-tail)}`;
}

export function shortAddr(a: string): string {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "";
}

export function formatTonnes(n: number | bigint): string {
  return Number(n).toLocaleString("en-US");
}
