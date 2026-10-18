import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { hashTypedData, hexToBytes, keccak256, type Address, type Hex } from "viem";
import {
  ATTESTATION_SCHEME,
  ATTESTATION_TYPES,
  aesGcmDecrypt,
  attestationDomain,
  attestationMessage,
  b64ToBytes,
  canonicalBytes,
  commitmentOf,
  mldsaVerify,
  parseBundle,
  pqSigHashOf,
  recoverAttestationSigner,
  recoverEcdsaSigner,
  sha256Hex,
  tamperOneDigit,
  verifyBundle,
  type OnchainBatch,
  type VerifyInput,
} from "../src/lib/verify";
import { roundHalfUpScaled, seeToUint } from "../src/lib/decimal";
import { cidFromDigest, digestFromCid } from "../src/lib/cid";
import { bytes32ToString, periodToUint32, reportHash, stringToBytes32, uint32ToPeriod, type Report } from "../src/lib/report";

interface Vector {
  chainId: number;
  attestationContract: Address;
  jcsCases: { name: string; input: unknown; canonical: string }[];
  seeCases: { x: number; repr: string; seeDeclared: number }[];
  document: Record<string, unknown> & { report: Report };
  canonical: string;
  commitment: Hex;
  attestor: Address;
  eip712: { domain: Record<string, unknown>; types: Record<string, { name: string; type: string }[]>; primaryType: string; message: Record<string, unknown>; digest: Hex };
  onchain: { installationId: Hex; period: number; commitment: Hex; seeDeclared: number; verifiedTonnes: number; riskScore: number; modelVersionHash: Hex; algId: number; pqSigHash: Hex };
  ecdsaSignature: Hex;
  exporter: { address: Address; reportDigest: Hex; signature: Hex };
  mldsaPublicKeyB64: string;
  mldsaSignatureB64: string;
  pqSigHash: Hex;
  keyB64url: string;
  bundle: unknown;
  bundleBytesB64: string;
  cidDigest: Hex;
  cid: string;
}

const v = JSON.parse(readFileSync(fileURLToPath(new URL("./vectors/bundle_vector.json", import.meta.url)), "utf8")) as Vector;
const dec = new TextDecoder();
const bundleBytes = b64ToBytes(v.bundleBytesB64);
const key = b64ToBytes(v.keyB64url);
const pkHash = keccak256(b64ToBytes(v.mldsaPublicKeyB64));

const onchainOf = (over: Partial<OnchainBatch> = {}): OnchainBatch => ({
  commitment: v.commitment,
  attestor: v.attestor,
  pqSigHash: v.pqSigHash,
  cidDigest: v.cidDigest,
  installationId: v.onchain.installationId,
  period: v.onchain.period,
  seeDeclared: v.onchain.seeDeclared,
  verifiedTonnes: BigInt(v.onchain.verifiedTonnes),
  riskScore: v.onchain.riskScore,
  modelVersionHash: v.onchain.modelVersionHash,
  algId: v.onchain.algId,
  ...over,
});
const context = { chainId: v.chainId, attestationContract: v.attestationContract };
type RunOver = Omit<Partial<VerifyInput>, "onchain"> & { onchain?: Partial<OnchainBatch> };
const run = (over: RunOver = {}) => {
  const { onchain, ...rest } = over;
  return verifyBundle({ bundleBytes, onchain: onchainOf(onchain), context, isPqKeyAnchored: async (h) => h === pkHash, ...rest });
};
const status = (r: Awaited<ReturnType<typeof run>>, id: string) => r.checks.find((c) => c.id === id)?.status;

describe("cross-language vector (Python rfc8785 / eth_account EIP-712 / PQClean ML-DSA-44 / AES-GCM -> JS)", () => {
  it("JCS output is byte-identical to Python rfc8785 (floats, escapes, UTF-16 key order)", () => {
    for (const c of v.jcsCases) expect(dec.decode(canonicalBytes(c.input)), c.name).toBe(c.canonical);
    expect(v.canonical).toContain('"directEmissionsTco2":1150,');
    expect(v.canonical).toContain('"gridFactorTco2PerMwh":0.42,');
    expect(dec.decode(canonicalBytes(v.document))).toBe(v.canonical);
  });

  it("keccak256(JCS(document)) equals the Python commitment", () => {
    expect(commitmentOf(canonicalBytes(v.document))).toBe(v.commitment);
  });

  it("the EIP-712 Attestation digest equals eth_account's, and the signature recovers the attestor", async () => {
    const domain = attestationDomain(v.chainId, v.attestationContract);
    const message = attestationMessage(onchainOf(), v.commitment);
    expect(hashTypedData({ domain, types: ATTESTATION_TYPES, primaryType: "Attestation", message })).toBe(v.eip712.digest);
    expect(await recoverAttestationSigner(domain, message, v.ecdsaSignature)).toBe(v.attestor);
    // cidDigest is deliberately not in the struct
    expect(ATTESTATION_TYPES.Attestation.map((f) => f.name)).not.toContain("cidDigest");
  });

  it("the exporter's EIP-191 signature over keccak256(JCS(report)) matches the Python digest and recovers the exporter", async () => {
    expect(reportHash(v.document.report)).toBe(v.exporter.reportDigest);
    expect(await recoverEcdsaSigner(v.exporter.reportDigest, v.exporter.signature)).toBe(v.exporter.address);
  });

  it("ML-DSA-44 signature from PQClean verifies in @noble/post-quantum over the canonical bytes", () => {
    const pk = b64ToBytes(v.mldsaPublicKeyB64);
    const sig = b64ToBytes(v.mldsaSignatureB64);
    expect(pk.length).toBe(1312);
    expect(sig.length).toBe(2420);
    expect(mldsaVerify(sig, canonicalBytes(v.document), pk)).toBe(true);
    expect(pqSigHashOf(sig)).toBe(v.pqSigHash);
  });

  it("AES-256-GCM (AAD = commitment) decrypts the Python ciphertext to the canonical bytes", async () => {
    const b = parseBundle(bundleBytes);
    expect(b.ecdsa.scheme).toBe(ATTESTATION_SCHEME);
    const pt = await aesGcmDecrypt(key, b64ToBytes(b.encryption.iv), b64ToBytes(b.encryption.ciphertext), hexToBytes(v.commitment));
    expect(dec.decode(pt)).toBe(v.canonical);
    await expect(aesGcmDecrypt(key, b64ToBytes(b.encryption.iv), b64ToBytes(b.encryption.ciphertext), new Uint8Array(32))).rejects.toThrow();
  });

  it("bundle sha256 equals cidDigest and the CIDv1 round-trips", () => {
    expect(sha256Hex(bundleBytes)).toBe(v.cidDigest);
    expect(cidFromDigest(v.cidDigest)).toBe(v.cid);
    expect(digestFromCid(v.cid)).toBe(v.cidDigest);
  });
});

describe("exact decimal SEE rounding (INTERFACES 9G)", () => {
  it("matches Python Decimal(repr(x)) half-up for every case, including 0.40045 -> 4005", () => {
    for (const c of v.seeCases) {
      expect(String(c.x), `repr ${c.repr}`).toBe(String(Number(c.repr)));
      expect(Number(roundHalfUpScaled(c.x, 4)), `x=${c.repr}`).toBe(c.seeDeclared);
    }
    expect(seeToUint(0.40045)).toBe(4005);
    expect(Math.round(0.40045 * 1e4)).toBe(4004); // the binary-float shortcut this replaces
    expect(seeToUint(1.00005)).toBe(10001);
    expect(seeToUint(0.00005)).toBe(1);
    expect(seeToUint(0.000049999)).toBe(0);
    expect(seeToUint(1e-7)).toBe(0);
    expect(seeToUint(1.5e-7)).toBe(0);
    expect(seeToUint(0.419)).toBe(4190);
    expect(seeToUint(0.30000000000000004)).toBe(3000);
    expect(() => seeToUint(-1)).toThrow();
    expect(() => seeToUint(Number.NaN)).toThrow();
  });
});

describe("verifyBundle", () => {
  it("with the key: every check passes, nothing sealed", async () => {
    const r = await run({ key });
    expect(r.checks.map((c) => `${c.id}:${c.status}`)).toEqual([
      "cid:pass", "bundleCommitment:pass", "ecdsaOnchain:pass", "pqHash:pass", "pqKeyAnchored:pass",
      "decrypt:pass", "commitment:pass", "ecdsaDoc:pass", "mldsa:pass", "fieldsMatch:pass",
    ]);
    expect(r.allValid).toBe(true);
  });

  it("without the key: public checks pass (EIP-712 over the on-chain commitment), document checks are sealed, not failed", async () => {
    const r = await run();
    expect(["cid", "bundleCommitment", "ecdsaOnchain", "pqHash", "pqKeyAnchored"].map((id) => status(r, id))).toEqual(Array(5).fill("pass"));
    expect(["decrypt", "commitment", "ecdsaDoc", "mldsa", "fieldsMatch"].map((id) => status(r, id))).toEqual(Array(5).fill("sealed"));
    expect(r.anyFailed).toBe(false);
    expect(r.sealed).toBe(true);
    expect(r.allValid).toBe(false);
  });

  it("one-digit tamper of the decrypted report fails commitment, ECDSA, ML-DSA and fields-match", async () => {
    const pretty = JSON.stringify(v.document, null, 2);
    const tampered = tamperOneDigit(pretty);
    let diffs = 0;
    for (let i = 0; i < pretty.length; i++) if (pretty[i] !== tampered[i]) diffs++;
    expect(tampered.length).toBe(pretty.length);
    expect(diffs).toBe(1);

    const doc = JSON.parse(tampered);
    const c2 = commitmentOf(canonicalBytes(doc));
    expect(c2).not.toBe(v.commitment);
    const domain = attestationDomain(v.chainId, v.attestationContract);
    expect(await recoverAttestationSigner(domain, attestationMessage(onchainOf(), c2), v.ecdsaSignature)).not.toBe(v.attestor);
    expect(mldsaVerify(b64ToBytes(v.mldsaSignatureB64), canonicalBytes(doc), b64ToBytes(v.mldsaPublicKeyB64))).toBe(false);

    const r = await run({ key, documentOverride: doc });
    expect(["commitment", "ecdsaDoc", "mldsa", "fieldsMatch"].map((id) => status(r, id))).toEqual(Array(4).fill("fail"));
    // the stored file and the on-chain-only checks are untouched
    expect(["cid", "pqHash", "ecdsaOnchain", "pqKeyAnchored", "decrypt"].map((id) => status(r, id))).toEqual(Array(5).fill("pass"));
    expect(r.allValid).toBe(false);
  });

  it("the EIP-712 signature binds period, installation, tonnes, risk, model and algId: a replay with altered numbers fails", async () => {
    for (const over of [
      { period: 202610 },
      { verifiedTonnes: 9000n },
      { riskScore: 13 },
      { seeDeclared: 3000 },
      { algId: 2 },
      { installationId: stringToBytes32("TR-BOF-0002") },
      { modelVersionHash: keccak256("0x01") },
      { pqSigHash: keccak256("0x02") },
    ] as Partial<OnchainBatch>[]) {
      const r = await run({ onchain: over });
      expect(status(r, "ecdsaOnchain"), JSON.stringify(over, (_, x) => (typeof x === "bigint" ? x.toString() : x))).toBe("fail");
    }
    // wrong domain: another chain, another verifying contract
    const otherChain = await verifyBundle({ bundleBytes, onchain: onchainOf(), context: { ...context, chainId: 1 }, isPqKeyAnchored: async () => true });
    expect(status(otherChain, "ecdsaOnchain")).toBe("fail");
    const otherContract = await verifyBundle({ bundleBytes, onchain: onchainOf(), context: { ...context, attestationContract: "0x" + "11".repeat(20) as Address }, isPqKeyAnchored: async () => true });
    expect(status(otherContract, "ecdsaOnchain")).toBe("fail");
  });

  it("an old EIP-191 style bundle (scheme downgrade) is rejected", async () => {
    const downgraded = new TextEncoder().encode(JSON.stringify({ ...(v.bundle as object), ecdsa: { ...(v.bundle as { ecdsa: object }).ecdsa, scheme: "secp256k1-eip191-keccak256" } }));
    const r = await verifyBundle({ bundleBytes: downgraded, onchain: onchainOf(), context, isPqKeyAnchored: async () => true });
    expect(status(r, "ecdsaOnchain")).toBe("fail");
    expect(r.checks.find((c) => c.id === "ecdsaOnchain")?.detail).toContain("unexpected signature scheme");
  });

  it("fieldsMatch binds every on-chain number to the signed document", async () => {
    const mut = (f: (d: any) => void, onchain: Partial<OnchainBatch> = {}) => { // eslint-disable-line @typescript-eslint/no-explicit-any
      const d = JSON.parse(JSON.stringify(v.document));
      f(d);
      return run({ key, documentOverride: d, onchain });
    };
    const row = (r: Awaited<ReturnType<typeof run>>) => r.checks.find((c) => c.id === "fieldsMatch")!;
    expect(row(await mut(() => {})).status).toBe("pass");
    expect(row(await mut((d) => { d.report.productionTonnes = 5001; })).detail).toContain("verifiedTonnes");
    expect(row(await mut((d) => { d.report.productionTonnes = 5000.5; })).detail).toContain("verifiedTonnes");
    expect(row(await mut((d) => { d.assessment.riskScore = 13; })).detail).toContain("riskScore");
    expect(row(await mut((d) => { d.assessment.modelVersionHash = "0x" + "cd".repeat(32); })).detail).toContain("modelVersionHash");
    expect(row(await mut((d) => { d.report.installationId = "TR-BOF-0002"; })).detail).toContain("installationId");
    expect(row(await mut((d) => { d.report.period = "2026-10"; })).detail).toContain("period");
    expect(row(await mut((d) => { d.report.period = "2026-9"; })).detail).toContain("period");
    expect(row(await mut((d) => { d.chainId = 1; })).detail).toContain("chainId");
    expect(row(await mut((d) => { d.attestationContract = "0x" + "11".repeat(20); })).detail).toContain("attestationContract");
    expect(row(await mut(() => {}, { seeDeclared: 3000 })).detail).toContain("seeDeclared");
    expect(row(await mut(() => {}, { riskScore: 99 })).detail).toContain("riskScore");
    // exact decimal rounding: a document declaring 0.40045 only matches an on-chain 4005 (the float shortcut would say 4004)
    expect(row(await mut((d) => { d.report.seeTco2PerT = 0.40045; }, { seeDeclared: 4005 })).detail).not.toContain("seeDeclared");
    expect(row(await mut((d) => { d.report.seeTco2PerT = 0.40045; }, { seeDeclared: 4004 })).detail).toContain("seeDeclared");
  });

  it("ML-DSA key anchoring uses registry.isAttestorPqKey(attestor, keccak(pk)): history-aware, unreadable means skipped", async () => {
    const ok = await run();
    expect(status(ok, "pqKeyAnchored")).toBe("pass");
    const unknownKey = await run({ isPqKeyAnchored: async () => false });
    expect(status(unknownKey, "pqKeyAnchored")).toBe("fail");
    expect(unknownKey.allValid).toBe(false);
    const asked: Hex[] = [];
    await run({ isPqKeyAnchored: async (h) => (asked.push(h), true) });
    expect(asked).toEqual([pkHash]);
    const unreadable = await verifyBundle({ bundleBytes, onchain: onchainOf(), context, key });
    expect(status(unreadable, "pqKeyAnchored")).toBe("skipped");
    expect(unreadable.allValid).toBe(false);
    const throwing = await run({ key, isPqKeyAnchored: async () => { throw new Error("rpc down"); } });
    expect(status(throwing, "pqKeyAnchored")).toBe("skipped");
  });

  it("re-serialising with different whitespace never changes the commitment (JCS)", async () => {
    const doc = JSON.parse(JSON.stringify(v.document, null, 7));
    expect((await run({ key, documentOverride: doc })).allValid).toBe(true);
  });

  it("wrong key fails decryption and skips the dependent checks", async () => {
    const r = await run({ key: new Uint8Array(32) });
    expect(status(r, "decrypt")).toBe("fail");
    expect(status(r, "mldsa")).toBe("skipped");
  });

  it("a swapped on-chain commitment, pqSigHash or stored bytes is caught without the key", async () => {
    const bad = keccak256("0x1234");
    const r1 = await run({ onchain: { commitment: bad } });
    expect(status(r1, "bundleCommitment")).toBe("fail");
    expect(status(r1, "ecdsaOnchain")).toBe("fail");
    expect(status(await run({ onchain: { pqSigHash: bad } }), "pqHash")).toBe("fail");
    const r3 = await verifyBundle({ bundleBytes: new Uint8Array([...bundleBytes, 32]), onchain: onchainOf(), context });
    expect(status(r3, "cid")).toBe("fail");
  });
});

describe("report helpers", () => {
  it("bytes32 ids and periods round-trip", () => {
    expect(bytes32ToString(stringToBytes32("TR-BOF-0002"))).toBe("TR-BOF-0002");
    expect(stringToBytes32("TR-EAF-0001")).toBe("0x54522d4541462d30303031" + "00".repeat(21));
    expect(periodToUint32("2026-09")).toBe(202609);
    expect(uint32ToPeriod(202609)).toBe("2026-09");
  });
});
