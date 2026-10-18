import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { keccak256, toBytes, type Address, type Hex } from "viem";
import { checkReasonText, verifyBundle, b64ToBytes, tamperOneDigit, type Check, type CheckId, type CheckStatus, type VerifyReport } from "../src/lib/verify";
import { failureSentence, verdictFor, type VerdictInput } from "../src/lib/verdict";
import { stringToBytes32 } from "../src/lib/report";

const ALL: CheckId[] = ["cid", "bundleCommitment", "ecdsaOnchain", "pqHash", "pqKeyAnchored", "decrypt", "commitment", "ecdsaDoc", "mldsa", "fieldsMatch"];
const report = (over: Partial<Record<CheckId, CheckStatus>> = {}, sealed = false): VerifyReport => {
  const checks: Check[] = ALL.map((id) => ({ id, status: over[id] ?? (sealed && ["decrypt", "commitment", "ecdsaDoc", "mldsa", "fieldsMatch"].includes(id) ? "sealed" : "pass"), detail: "" }));
  const anyFailed = checks.some((c) => c.status === "fail");
  const isSealed = checks.some((c) => c.status === "sealed");
  return { checks, anyFailed, sealed: isSealed, allValid: !anyFailed && !isSealed && checks.every((c) => c.status === "pass") };
};
const base: Omit<VerdictInput, "report"> = { status: "Attested", revoked: false, attestedTonnes: 4000n, allocatedTonnes: 1200n };

describe("plain-language verdict", () => {
  it("a valid keyed certificate says who signed, that the report is unchanged, and how much is allocated", () => {
    const v = verdictFor({ ...base, report: report() });
    expect(v.tone).toBe("green");
    expect(v.title).toBe("Valid certificate");
    expect(v.text).toBe("signed by the EmbedProof attestor (ECDSA + ML-DSA), report unchanged since signing, 1,200 of 4,000 t allocated.");
  });

  it("a failure names what failed in one sentence", () => {
    const v = verdictFor({ ...base, report: report({ commitment: "fail", ecdsaDoc: "fail", mldsa: "fail", fieldsMatch: "fail" }) });
    expect(v.tone).toBe("red");
    expect(v.title).toBe("Verification failed");
    expect(v.text).toBe("the report no longer matches what the attestor signed; and the report's numbers differ from the on-chain record.");
    expect(verdictFor({ ...base, report: report({ commitment: "fail", ecdsaDoc: "fail", mldsa: "fail" }), edited: true }).text).toContain("this edited copy no longer matches");
  });

  it("each kind of failure has its own sentence", () => {
    const f = (id: CheckId) => failureSentence(report({ [id]: "fail" } as Partial<Record<CheckId, CheckStatus>>).checks);
    expect(f("cid")).toContain("stored bundle does not match");
    expect(f("ecdsaOnchain")).toContain("ECDSA signature does not match");
    expect(f("pqKeyAnchored")).toContain("not one anchored on-chain");
    expect(f("pqHash")).toContain("post-quantum signature does not match");
    expect(f("decrypt")).toContain("could not be decrypted");
  });

  it("sealed reports are valid but say the report itself is sealed", () => {
    const v = verdictFor({ ...base, report: report({}, true) });
    expect(v.tone).toBe("green");
    expect(v.title).toBe("Signatures valid");
    expect(v.text).toBe("signed by the EmbedProof attestor (ECDSA + ML-DSA), 1,200 of 4,000 t allocated. The report itself is sealed: open the certificate link to check it.");
  });

  it("revoked, rejected and pending batches are amber even when every signature is valid", () => {
    expect(verdictFor({ ...base, report: report(), revoked: true, status: "Rejected" }).tone).toBe("amber");
    expect(verdictFor({ ...base, report: report(), status: "PendingVerifier" }).title).toBe("Signatures valid, waiting for a verifier");
    expect(verdictFor({ ...base, report: report(), status: "Rejected" }).title).toBe("Signatures valid, batch rejected");
  });

  it("a missing bundle and a pending run are explained, not hidden", () => {
    expect(verdictFor({ ...base, report: null, bundleError: "Bundle not found (404)" }).title).toBe("Cannot verify yet");
    expect(verdictFor({ ...base, report: null }).tone).toBe("neutral");
  });

  it("a reason text that does not match its on-chain hash turns the verdict red", () => {
    const bad = checkReasonText("tampered", keccak256(toBytes("original")));
    const v = verdictFor({ ...base, report: report(), extraChecks: [bad] });
    expect(v.tone).toBe("red");
    expect(v.text).toContain("reason text does not match its on-chain hash");
  });
});

describe("reason text hash check (same encoding as the attestor: keccak256 of the raw UTF-8 bytes)", () => {
  it("matches Python's keccak256(text.encode('utf-8')), including non-ASCII text", () => {
    expect(keccak256(toBytes("abc"))).toBe("0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45");
    const long = "Override: a blast-furnace route cannot emit at EAF level (declared 0.56 vs 2.17-2.35 expected). Site visit required, çelik.";
    const c = checkReasonText(long, "0x354ad8f23de95810153a8da0cc0e5b634bf2cc8c5ef1eace067ca2be87eff0f4");
    expect(c.status).toBe("pass");
  });

  it("fails on any change, including a trailing space, and ignores hex case", () => {
    const h = "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45" as Hex;
    expect(checkReasonText("abc", h).status).toBe("pass");
    expect(checkReasonText("abc", h.toUpperCase().replace("0X", "0x") as Hex).status).toBe("pass");
    expect(checkReasonText("abc ", h).status).toBe("fail");
    expect(checkReasonText("abd", h).status).toBe("fail");
  });
});

describe("verdict over the real verifyBundle output (Python vector)", () => {
  const v = JSON.parse(readFileSync(fileURLToPath(new URL("./vectors/bundle_vector.json", import.meta.url)), "utf8")) as {
    chainId: number; attestationContract: Address; document: unknown; commitment: Hex; attestor: Address; pqSigHash: Hex; cidDigest: Hex;
    onchain: { installationId: Hex; period: number; seeDeclared: number; verifiedTonnes: number; riskScore: number; modelVersionHash: Hex; algId: number };
    bundleBytesB64: string; keyB64url: string; mldsaPublicKeyB64: string;
  };
  const onchain = { commitment: v.commitment, attestor: v.attestor, pqSigHash: v.pqSigHash, cidDigest: v.cidDigest, ...v.onchain, verifiedTonnes: BigInt(v.onchain.verifiedTonnes), installationId: stringToBytes32("TR-EAF-0001") };
  const pkHash = keccak256(b64ToBytes(v.mldsaPublicKeyB64));
  const run = (documentOverride?: unknown) =>
    verifyBundle({ bundleBytes: b64ToBytes(v.bundleBytesB64), onchain, context: { chainId: v.chainId, attestationContract: v.attestationContract }, key: b64ToBytes(v.keyB64url), isPqKeyAnchored: async (h) => h === pkHash, documentOverride });

  it("untampered: valid certificate; one digit changed: verification failed", async () => {
    const ok = verdictFor({ report: await run(), status: "Attested", revoked: false, attestedTonnes: 5000n, allocatedTonnes: 0n });
    expect(ok.title).toBe("Valid certificate");
    expect(ok.text).toContain("0 of 5,000 t allocated");
    const doc = JSON.parse(tamperOneDigit(JSON.stringify(v.document, null, 2)));
    const bad = verdictFor({ report: await run(doc), status: "Attested", revoked: false, attestedTonnes: 5000n, edited: true });
    expect(bad.tone).toBe("red");
    expect(bad.text).toContain("this edited copy no longer matches what the attestor signed");
  });
});
