import { describe, expect, it } from "vitest";
import type { Hex } from "viem";
import type { AllocationView, BatchView } from "../src/lib/chainReads";
import { buildCertificatePdf } from "../src/lib/pdf";
import { stringToBytes32 } from "../src/lib/report";

const h = (c: string) => ("0x" + c.repeat(32)) as Hex;

describe("certificate PDF (jsPDF + QR)", () => {
  it("renders an A4 PDF in memory with the SEE, tonnes and a QR image carrying the full #k= link", async () => {
    const batch = {
      batchId: 1n, installationId: stringToBytes32("TR-EAF-0001"), installation: "TR-EAF-0001", period: 202609, seeDeclared: 4190, verifiedTonnes: 5000n,
      riskScore: 12, algId: 1, status: 2, statusName: "Attested", attestedAt: 1_790_000_000, commitment: h("aa"), modelVersionHash: h("bb"),
      pqSigHash: h("cc"), cidDigest: h("dd"), attestor: "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", verifier: "0x0000000000000000000000000000000000000000", reasonHash: h("00"),
    } as BatchView;
    const allocation: AllocationView = {
      batchId: 1n, consignmentId: stringToBytes32("CN-1-A"), importer: "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc", cnCode: 72083900, tonnes: 5000n, claimed: true,
    };
    const url = "http://localhost:3000/verify/1#k=btTCaoCXuqxkiQZt_k9ANc9ySZLmblIGgrOGjjqmrPw";
    const doc = await buildCertificatePdf({
      batch, allocation, installationName: "Marmara Demir Mini-Mill (fictional)", importerLabel: "Importer A", verifyUrl: url,
      chainLabel: "Local Anvil", cid: "bafkreiexample", explanation: "Declared 0.419 sits inside the expected EAF range.",
      pqKeyHash: h("ab"), pqAnchored: true,
    });
    const bytes = new Uint8Array(doc.output("arraybuffer"));
    expect(new TextDecoder().decode(bytes.slice(0, 5))).toBe("%PDF-");
    expect(doc.getNumberOfPages()).toBe(1);
    expect(bytes.length).toBeGreaterThan(8_000); // an embedded PNG QR makes it much larger than a text-only page
    const text = new TextDecoder("latin1").decode(bytes);
    expect(text).toContain("/Subtype /Image");
    expect(text).toContain("5,000 attested tonnes");
    expect(text).not.toContain("verified tonnes");
    expect(text).toContain("ML-DSA PUBLIC KEY HASH");
    expect(text).toContain("ML-DSA KEY ANCHORED ON-CHAIN");
    expect(text).toContain("yes, the registry lists this key for the attestor");
  });
});
