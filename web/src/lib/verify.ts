/**
 * Pure verification functions for an EmbedProof certificate (docs/INTERFACES.md sections 5 and 9).
 * No React, no network, no globals beyond WebCrypto (`crypto.subtle`): runs identically in the
 * browser and in node (tests prove cross-language equality with Python).
 */
import canonicalize from "canonicalize";
import { ml_dsa44 } from "@noble/post-quantum/ml-dsa.js";
import { sha256 } from "@noble/hashes/sha2.js";
import {
  bytesToHex,
  hexToBytes,
  isAddressEqual,
  keccak256,
  recoverMessageAddress,
  recoverTypedDataAddress,
  type Address,
  type Hex,
  type TypedDataDomain,
} from "viem";
import { seeToUint } from "./decimal";

const utf8 = new TextEncoder();
const utf8d = new TextDecoder("utf-8", { fatal: true });

// ---------- encoding helpers ----------

/** Base64 or base64url (padding optional) -> bytes. */
export function b64ToBytes(s: string): Uint8Array {
  const std = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(std.padEnd(Math.ceil(std.length / 4) * 4, "="));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function bytesToB64(b: Uint8Array): string {
  let s = "";
  for (const x of b) s += String.fromCharCode(x);
  return btoa(s);
}

/** `#k=<base64url key>` (or `k=...`) -> 32-byte key, or null. */
export function parseKeyFromHash(hash: string): Uint8Array | null {
  const m = /(?:^#?|&)k=([A-Za-z0-9_-]+)/.exec(hash);
  if (!m) return null;
  try {
    const k = b64ToBytes(m[1]);
    return k.length === 32 ? k : null;
  } catch {
    return null;
  }
}

// ---------- primitives ----------

/** RFC 8785 JCS -> UTF-8 bytes. */
export function canonicalBytes(doc: unknown): Uint8Array {
  const s = canonicalize(doc);
  if (s === undefined) throw new Error("value cannot be canonicalized");
  return utf8.encode(s);
}

export const commitmentOf = (canonical: Uint8Array): Hex => keccak256(canonical);
export const sha256Hex = (bytes: Uint8Array): Hex => bytesToHex(sha256(bytes));
export const pqSigHashOf = (sig: Uint8Array): Hex => keccak256(sig);

/** EIP-191 personal_sign over 32 raw bytes (the EXPORTER's signature over keccak256(JCS(report))); null if malformed. */
export async function recoverEcdsaSigner(hash: Hex, signature: Hex): Promise<Address | null> {
  try {
    return await recoverMessageAddress({ message: { raw: hash }, signature });
  } catch {
    return null;
  }
}

// ---------- EIP-712 attestor signature (INTERFACES section 9A) ----------

export const ATTESTATION_SCHEME = "eip712-embedproof-attestation-v1";

/** `cidDigest` is deliberately not part of the struct: the bundle carries the signature, so it cannot sign its own digest. */
export const ATTESTATION_TYPES = {
  Attestation: [
    { name: "installationId", type: "bytes32" },
    { name: "period", type: "uint32" },
    { name: "commitment", type: "bytes32" },
    { name: "seeDeclared", type: "uint32" },
    { name: "verifiedTonnes", type: "uint64" },
    { name: "riskScore", type: "uint8" },
    { name: "modelVersionHash", type: "bytes32" },
    { name: "algId", type: "uint8" },
    { name: "pqSigHash", type: "bytes32" },
  ],
} as const;

export function attestationDomain(chainId: number, verifyingContract: Address): TypedDataDomain {
  return { name: "EmbedProof", version: "1", chainId, verifyingContract };
}

/** The struct the attestor signed, rebuilt from the ON-CHAIN batch plus a commitment (on-chain, or recomputed from the document). */
export function attestationMessage(b: OnchainBatch, commitment: Hex) {
  return {
    installationId: b.installationId,
    period: b.period,
    commitment,
    seeDeclared: b.seeDeclared,
    verifiedTonnes: BigInt(b.verifiedTonnes),
    riskScore: b.riskScore,
    modelVersionHash: b.modelVersionHash,
    algId: b.algId,
    pqSigHash: b.pqSigHash,
  };
}

export async function recoverAttestationSigner(domain: TypedDataDomain, message: ReturnType<typeof attestationMessage>, signature: Hex): Promise<Address | null> {
  try {
    return await recoverTypedDataAddress({ domain, types: ATTESTATION_TYPES, primaryType: "Attestation", message, signature });
  } catch {
    return null;
  }
}

export const MLDSA44_PK_BYTES = 1312;
export const MLDSA44_SIG_BYTES = 2420;

/**
 * ML-DSA-44 (FIPS 204 final, pure, empty context) verify.
 * @noble/post-quantum 0.7.x argument order is verify(signature, message, publicKey), NOT (pk, msg, sig).
 */
export function mldsaVerify(signature: Uint8Array, message: Uint8Array, publicKey: Uint8Array): boolean {
  try {
    if (publicKey.length !== MLDSA44_PK_BYTES || signature.length !== MLDSA44_SIG_BYTES) return false;
    return ml_dsa44.verify(signature, message, publicKey);
  } catch {
    return false;
  }
}

/** AES-256-GCM decrypt; `ciphertext` is ciphertext||16-byte tag; AAD = the 32 raw commitment bytes. */
export async function aesGcmDecrypt(key: Uint8Array, iv: Uint8Array, ciphertext: Uint8Array, aad: Uint8Array): Promise<Uint8Array> {
  const subtle = globalThis.crypto.subtle;
  const k = await subtle.importKey("raw", key as BufferSource, "AES-GCM", false, ["decrypt"]);
  const pt = await subtle.decrypt({ name: "AES-GCM", iv: iv as BufferSource, additionalData: aad as BufferSource }, k, ciphertext as BufferSource);
  return new Uint8Array(pt);
}

// ---------- bundle ----------

export interface Bundle {
  schema: string;
  algId: number;
  batchHint?: { chainId: number; installationId: string; period: number };
  commitment: Hex;
  ecdsa: { scheme: string; signer: Address; signature: Hex };
  mldsa: { scheme: string; backend: string; publicKey: string; signature: string };
  encryption: { alg: string; iv: string; aad: string; ciphertext: string };
}

export function parseBundle(bytes: Uint8Array): Bundle {
  const j = JSON.parse(utf8d.decode(bytes)) as Bundle;
  if (
    j?.schema !== "embedproof.bundle.v1" ||
    !j.ecdsa?.signature ||
    !j.mldsa?.signature ||
    !j.mldsa?.publicKey ||
    !j.encryption?.ciphertext ||
    !j.commitment
  ) {
    throw new Error("not an embedproof.bundle.v1 file");
  }
  return j;
}

// ---------- full check pipeline ----------

export interface OnchainBatch {
  commitment: Hex;
  attestor: Address;
  pqSigHash: Hex;
  cidDigest: Hex;
  /** bytes32 installation id as stored on-chain */
  installationId: Hex;
  period: number;
  seeDeclared: number;
  verifiedTonnes: bigint | number;
  riskScore: number;
  modelVersionHash: Hex;
  algId: number;
}

export type CheckStatus = "pass" | "fail" | "sealed" | "skipped";
export type CheckId =
  | "cid"
  | "bundleCommitment"
  | "ecdsaOnchain"
  | "pqHash"
  | "pqKeyAnchored"
  | "decrypt"
  | "commitment"
  | "ecdsaDoc"
  | "mldsa"
  | "fieldsMatch";

export interface Check {
  id: CheckId;
  status: CheckStatus;
  /** short technical detail */
  detail: string;
}

export interface VerifyReport {
  checks: Check[];
  /** true when every check passed and nothing is sealed */
  allValid: boolean;
  anyFailed: boolean;
  sealed: boolean;
  /** decrypted attestation document (JSON) when a key was supplied and decryption worked */
  document?: unknown;
  recomputedCommitment?: Hex;
  recoveredSigner?: Address | null;
  recoveredSignerOnchain?: Address | null;
  bundle?: Bundle;
}

/** The EIP-712 domain and the document binding both need the chain id and the attestation proxy address. */
export interface VerifyContext {
  chainId: number;
  attestationContract: Address;
}

export interface VerifyInput {
  bundleBytes: Uint8Array;
  onchain: OnchainBatch;
  context: VerifyContext;
  /** 32-byte AES key from the `#k=` fragment; omit for the sealed (public-only) checks */
  key?: Uint8Array | null;
  /** (possibly tampered) document to check instead of the decrypted one: the tamper test */
  documentOverride?: unknown;
  /** registry.isAttestorPqKey(attestor, keccak256(publicKey)); omit if it cannot be read (the check is then skipped) */
  isPqKeyAnchored?: (keyHash: Hex) => Promise<boolean>;
}

const eqHex = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

export async function verifyBundle(input: VerifyInput): Promise<VerifyReport> {
  const { bundleBytes, onchain, key, documentOverride, context, isPqKeyAnchored } = input;
  const checks: Check[] = [];
  const push = (id: CheckId, ok: boolean, detail: string) => checks.push({ id, status: ok ? "pass" : "fail", detail });
  const domain = attestationDomain(context.chainId, context.attestationContract);

  // 1. the stored file is the file the chain points at
  const digest = sha256Hex(bundleBytes);
  push("cid", eqHex(digest, onchain.cidDigest), `sha256(bundle) = ${digest}`);

  let bundle: Bundle;
  try {
    bundle = parseBundle(bundleBytes);
  } catch (e) {
    checks.push({ id: "bundleCommitment", status: "fail", detail: (e as Error).message });
    return finish(checks, { sealed: false });
  }

  // 2. commitment recorded in the bundle equals the on-chain one
  push("bundleCommitment", eqHex(bundle.commitment, onchain.commitment), `bundle ${bundle.commitment}`);

  // 3. EIP-712 attestor signature over the struct rebuilt from the ON-CHAIN batch (needs no key)
  const recoveredOnchain = await recoverAttestationSigner(domain, attestationMessage(onchain, onchain.commitment), bundle.ecdsa.signature);
  const schemeOk = bundle.ecdsa.scheme === ATTESTATION_SCHEME;
  push(
    "ecdsaOnchain",
    schemeOk && !!recoveredOnchain && isAddressEqual(recoveredOnchain, onchain.attestor),
    schemeOk ? `EIP-712 recovered ${recoveredOnchain ?? "invalid signature"}` : `unexpected signature scheme "${bundle.ecdsa.scheme}" (want ${ATTESTATION_SCHEME})`,
  );

  // 4. keccak256(ML-DSA signature bytes) equals the on-chain pqSigHash (needs no key)
  let sigBytes: Uint8Array | null = null;
  let pk: Uint8Array | null = null;
  try {
    sigBytes = b64ToBytes(bundle.mldsa.signature);
    pk = b64ToBytes(bundle.mldsa.publicKey);
  } catch {
    /* reported through the pqHash check below */
  }
  const pqHash = sigBytes ? pqSigHashOf(sigBytes) : null;
  push("pqHash", !!pqHash && eqHex(pqHash, onchain.pqSigHash), pqHash ? `keccak(sig) = ${pqHash}` : "malformed signature encoding");

  // 4b. the ML-DSA public key inside the bundle is a key the registry has anchored for this attestor
  //     (INTERFACES 9E: registry.isAttestorPqKey keeps history, so key rotation does not break old certificates)
  if (!pk) {
    checks.push({ id: "pqKeyAnchored", status: "fail", detail: "malformed public key encoding" });
  } else if (!isPqKeyAnchored) {
    checks.push({ id: "pqKeyAnchored", status: "skipped", detail: "registry.isAttestorPqKey could not be read" });
  } else {
    const pkHash = keccak256(pk);
    try {
      push("pqKeyAnchored", await isPqKeyAnchored(pkHash), `keccak(publicKey) = ${pkHash}`);
    } catch {
      checks.push({ id: "pqKeyAnchored", status: "skipped", detail: "registry.isAttestorPqKey could not be read" });
    }
  }

  // ---- sealed path: no key, the document stays encrypted ----
  if (!key) {
    for (const id of ["decrypt", "commitment", "ecdsaDoc", "mldsa", "fieldsMatch"] as const) {
      checks.push({ id, status: "sealed", detail: "sealed: open with the certificate link" });
    }
    return finish(checks, { sealed: true, bundle, recoveredSignerOnchain: recoveredOnchain });
  }

  // ---- keyed path ----
  let document: unknown;
  try {
    const plaintext = await aesGcmDecrypt(key, b64ToBytes(bundle.encryption.iv), b64ToBytes(bundle.encryption.ciphertext), hexToBytes(onchain.commitment));
    document = JSON.parse(utf8d.decode(plaintext));
    checks.push({ id: "decrypt", status: "pass", detail: `AES-256-GCM, ${plaintext.length} bytes, AAD = commitment` });
  } catch {
    checks.push({ id: "decrypt", status: "fail", detail: "wrong key or modified ciphertext" });
    for (const id of ["commitment", "ecdsaDoc", "mldsa", "fieldsMatch"] as const) {
      checks.push({ id, status: "skipped", detail: "needs the decrypted document" });
    }
    return finish(checks, { sealed: false, bundle, recoveredSignerOnchain: recoveredOnchain });
  }

  const doc = documentOverride !== undefined ? documentOverride : document;
  let canonical: Uint8Array | null = null;
  try {
    canonical = canonicalBytes(doc);
  } catch {
    canonical = null;
  }
  if (!canonical) {
    for (const id of ["commitment", "ecdsaDoc", "mldsa", "fieldsMatch"] as const) {
      checks.push({ id, status: "fail", detail: "document cannot be canonicalized" });
    }
    return finish(checks, { sealed: false, bundle, document, recoveredSignerOnchain: recoveredOnchain });
  }

  // 6. recompute keccak256(JCS(document)) and compare with the chain
  const recomputed = commitmentOf(canonical);
  push("commitment", eqHex(recomputed, onchain.commitment), `recomputed ${recomputed}`);

  // 7. the EIP-712 signature must still recover the attestor when the struct carries the RECOMPUTED commitment
  const recovered = await recoverAttestationSigner(domain, attestationMessage(onchain, recomputed), bundle.ecdsa.signature);
  push("ecdsaDoc", schemeOk && !!recovered && isAddressEqual(recovered, onchain.attestor), `EIP-712 recovered ${recovered ?? "invalid signature"}`);

  // 8. ML-DSA-44 over the canonical bytes with the bundle's public key
  const pqOk = !!sigBytes && !!pk && mldsaVerify(sigBytes, canonical, pk);
  push("mldsa", pqOk, `ML-DSA-44 verify over ${canonical.length} canonical bytes`);

  // 9. "fields match chain": the signatures cover commitment-level data only, so this ties the
  //    on-chain numbers (SEE, tonnes, risk, model, ids) to the signed document
  const fields = checkFieldsMatch(doc, onchain, context);
  push("fieldsMatch", fields.ok, fields.detail);

  return finish(checks, {
    sealed: false,
    bundle,
    document,
    recomputedCommitment: recomputed,
    recoveredSigner: recovered,
    recoveredSignerOnchain: recoveredOnchain,
  });
}

function finish(checks: Check[], extra: Partial<VerifyReport> & { sealed: boolean }): VerifyReport {
  const anyFailed = checks.some((c) => c.status === "fail");
  const sealed = extra.sealed || checks.some((c) => c.status === "sealed");
  const allValid = !anyFailed && !sealed && checks.every((c) => c.status === "pass");
  return { ...extra, checks, anyFailed, sealed, allValid };
}

/**
 * The signed document must describe the same batch the chain records (INTERFACES.md section 6 `fieldsMatch`):
 * installationId (bytes32 ASCII), period YYYY-MM -> YYYYMM, exact-decimal round_half_up(see x 1e4) == seeDeclared,
 * productionTonnes == verifiedTonnes, assessment.riskScore == riskScore, assessment.modelVersionHash ==
 * modelVersionHash, plus document.chainId and document.attestationContract.
 */
export function checkFieldsMatch(doc: unknown, onchain: OnchainBatch, context: VerifyContext): { ok: boolean; detail: string } {
  const d = doc as {
    report?: Record<string, unknown>;
    assessment?: Record<string, unknown>;
    chainId?: unknown;
    attestationContract?: unknown;
  } | null;
  const r = d?.report;
  if (!r || typeof r !== "object") return { ok: false, detail: "document has no report" };
  const a = d?.assessment;
  const problems: string[] = [];
  const idStr = new TextDecoder().decode(hexToBytes(onchain.installationId)).replace(/\0+$/, "");
  if (r.installationId !== idStr) problems.push("installationId");
  const period = typeof r.period === "string" && /^\d{4}-\d{2}$/.test(r.period) ? Number(r.period.replace("-", "")) : NaN;
  if (period !== onchain.period) problems.push("period");
  let see = NaN;
  try {
    see = typeof r.seeTco2PerT === "number" ? seeToUint(r.seeTco2PerT) : NaN;
  } catch {
    see = NaN;
  }
  if (see !== onchain.seeDeclared) problems.push("seeDeclared");
  if (typeof r.productionTonnes !== "number" || !Number.isInteger(r.productionTonnes) || BigInt(r.productionTonnes) !== BigInt(onchain.verifiedTonnes)) {
    problems.push("verifiedTonnes");
  }
  if (!a || typeof a !== "object" || a.riskScore !== onchain.riskScore) problems.push("riskScore");
  if (!a || typeof a.modelVersionHash !== "string" || a.modelVersionHash.toLowerCase() !== onchain.modelVersionHash.toLowerCase()) problems.push("modelVersionHash");
  if (d?.chainId !== context.chainId) problems.push("chainId");
  if (typeof d?.attestationContract !== "string" || d.attestationContract.toLowerCase() !== context.attestationContract.toLowerCase()) {
    problems.push("attestationContract");
  }
  return problems.length
    ? { ok: false, detail: `mismatch: ${problems.join(", ")}` }
    : { ok: true, detail: "installationId, period, SEE, tonnes, riskScore, modelVersionHash, chainId, contract all equal the on-chain batch" };
}

/** Tamper helper: change exactly one digit of the declared SEE in pretty-printed attestation JSON. */
export function tamperOneDigit(prettyJson: string): string {
  const re = /("seeTco2PerT"\s*:\s*\d*\.?)(\d*)(\d)/;
  const m = re.exec(prettyJson);
  if (m) {
    const d = Number(m[3]);
    const nd = d > 0 ? d - 1 : d + 1;
    return prettyJson.replace(re, `$1$2${nd}`);
  }
  // fallback: first digit anywhere
  return prettyJson.replace(/\d/, (c) => String((Number(c) + 1) % 10));
}
