import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { hashTypedData, keccak256, type Address, type Hex } from "viem";
import {
  ATTESTATION_SCHEME,
  ATTESTATION_TYPES,
  attestationDomain,
  attestationMessage,
  b64ToBytes,
  canonicalBytes,
  commitmentOf,
  mldsaVerify,
  pqSigHashOf,
  recoverAttestationSigner,
  recoverEcdsaSigner,
  sha256Hex,
  verifyBundle,
  type OnchainBatch,
} from "../src/lib/verify";
import { cidFromDigest } from "../src/lib/cid";
import { roundHalfUpScaled, seeToUint } from "../src/lib/decimal";
import { reportHash, stringToBytes32, type Report } from "../src/lib/report";

// The attestor build publishes its own vector (Python: rfc8785, eth_account, PQClean, AESGCM).
// This proves the web verifier agrees with the REAL attestor output, not just with web/tests/gen_vector.py.
// Until the attestor regenerates it for v1.1 (EIP-712) the signature-scheme dependent tests are skipped.
const path = fileURLToPath(new URL("../../attestor/tests/vectors/bundle_vector.json", import.meta.url));

interface AttestorVector {
  chainId: number;
  attestationContract: Address;
  report: Report;
  exporter: { address: Address; reportDigest: Hex; signature: Hex };
  document: { report: Report; assessment: { riskScore: number; modelVersionHash: Hex }; chainId: number; attestationContract: Address };
  canonical: string;
  commitment: Hex;
  ecdsa: { scheme?: string; signer: Address; signature: Hex };
  mldsa: { publicKey: string; signature: string };
  pqSigHash: Hex;
  aesKeyB64url: string;
  bundleJson: string;
  cid: string;
  cidDigest: Hex;
  tampered: { document: unknown; commitment: Hex };
  eip712?: { digest: Hex };
  pqKeyHash?: Hex;
  seeRounding?: { cases: { see: string; seeDeclared: number }[] };
}

const present = existsSync(path);
const v = (present ? JSON.parse(readFileSync(path, "utf8")) : {}) as AttestorVector;
const eip712 = present && (v.ecdsa?.scheme === ATTESTATION_SCHEME);
if (present && !eip712) console.log(`attestor vector scheme is "${v.ecdsa?.scheme}": EIP-712 tests skipped until it is regenerated for v1.1`);

describe.skipIf(!present)("attestor/tests/vectors/bundle_vector.json: scheme independent parts", () => {
  const bundleBytes = new TextEncoder().encode(v.bundleJson);

  it("JCS, commitment, ML-DSA-44, pqSigHash, sha256/CID all agree with the attestor", () => {
    expect(new TextDecoder().decode(canonicalBytes(v.document))).toBe(v.canonical);
    expect(commitmentOf(canonicalBytes(v.document))).toBe(v.commitment);
    const sig = b64ToBytes(v.mldsa.signature);
    expect(mldsaVerify(sig, canonicalBytes(v.document), b64ToBytes(v.mldsa.publicKey))).toBe(true);
    expect(pqSigHashOf(sig)).toBe(v.pqSigHash);
    expect(sha256Hex(bundleBytes)).toBe(v.cidDigest);
    expect(cidFromDigest(v.cidDigest)).toBe(v.cid);
  });

  it("the exporter's keccak256(JCS(report)) matches the attestor's reportDigest and its signature recovers the exporter", async () => {
    expect(reportHash(v.report)).toBe(v.exporter.reportDigest);
    expect(await recoverEcdsaSigner(v.exporter.reportDigest, v.exporter.signature)).toBe(v.exporter.address);
  });
});

describe.skipIf(!eip712)("attestor vector (EIP-712, INTERFACES v1.1)", () => {
  const bundleBytes = new TextEncoder().encode(v.bundleJson);
  const doc = v.document;
  const onchain = (): OnchainBatch => ({
    commitment: v.commitment,
    attestor: v.ecdsa.signer,
    pqSigHash: v.pqSigHash,
    cidDigest: v.cidDigest,
    installationId: stringToBytes32(doc.report.installationId),
    period: Number(doc.report.period.replace("-", "")),
    seeDeclared: seeToUint(doc.report.seeTco2PerT),
    verifiedTonnes: BigInt(doc.report.productionTonnes),
    riskScore: doc.assessment.riskScore,
    modelVersionHash: doc.assessment.modelVersionHash,
    algId: 1,
  });
  const context = () => ({ chainId: v.chainId, attestationContract: v.attestationContract });
  const pkHash = () => keccak256(b64ToBytes(v.mldsa.publicKey));

  it("the EIP-712 signature recovers the attestor from the struct rebuilt from chain-style fields; digest and pq key hash agree", async () => {
    const domain = attestationDomain(v.chainId, v.attestationContract);
    const message = attestationMessage(onchain(), v.commitment);
    expect(await recoverAttestationSigner(domain, message, v.ecdsa.signature)).toBe(v.ecdsa.signer);
    if (v.eip712) expect(hashTypedData({ domain, types: ATTESTATION_TYPES, primaryType: "Attestation", message })).toBe(v.eip712.digest);
    if (v.pqKeyHash) expect(pkHash()).toBe(v.pqKeyHash);
  });

  it("the attestor's seeDeclared rounding cases match exact decimal half-up", () => {
    for (const c of v.seeRounding?.cases ?? []) expect(Number(roundHalfUpScaled(Number(c.see), 4)), `see=${c.see}`).toBe(c.seeDeclared);
    expect((v.seeRounding?.cases ?? []).length).toBeGreaterThan(5);
  });

  it("full pipeline with the key passes; the attestor's one-digit tamper fails commitment, ECDSA, ML-DSA and fields", async () => {
    const key = b64ToBytes(v.aesKeyB64url);
    const input = { bundleBytes, onchain: onchain(), context: context(), key, isPqKeyAnchored: async (h: Hex) => h === pkHash() };
    const ok = await verifyBundle(input);
    expect(ok.checks.filter((c) => c.status !== "pass")).toEqual([]);
    const bad = await verifyBundle({ ...input, documentOverride: v.tampered.document });
    const by = Object.fromEntries(bad.checks.map((c) => [c.id, c.status]));
    expect(by).toMatchObject({ commitment: "fail", ecdsaDoc: "fail", mldsa: "fail", fieldsMatch: "fail" });
    expect(commitmentOf(canonicalBytes(v.tampered.document))).toBe(v.tampered.commitment);
  });

  it("without the key the public checks pass and the rest is sealed", async () => {
    const r = await verifyBundle({ bundleBytes, onchain: onchain(), context: context(), isPqKeyAnchored: async (h) => h === pkHash() });
    expect(r.anyFailed).toBe(false);
    expect(r.sealed).toBe(true);
  });
});
