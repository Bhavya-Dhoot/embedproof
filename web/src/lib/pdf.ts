import type { Hex } from "viem";
import type { AllocationView, BatchView } from "./chainReads";
import { bandFor, bytes32ToPrintable, formatSee, formatTonnes, uint32ToPeriod } from "./report";

export interface CertificateInput {
  batch: BatchView;
  allocation: AllocationView;
  installationName?: string;
  importerLabel?: string;
  /** full verify URL including the #k= fragment */
  verifyUrl: string;
  chainLabel: string;
  cid: string;
  txHash?: Hex;
  explanation?: string;
  /** keccak256 of the bundle's ML-DSA public key, and whether the registry anchors it for the attestor */
  pqKeyHash?: Hex;
  pqAnchored?: boolean;
}

/** A4 certificate with a QR code of the full verify link (jsPDF + qrcode are loaded lazily). Returns the document without saving it. */
export async function buildCertificatePdf(c: CertificateInput) {
  const [{ jsPDF }, QR] = await Promise.all([import("jspdf"), import("qrcode")]);
  const qr = await QR.toDataURL(c.verifyUrl, { margin: 1, width: 480, errorCorrectionLevel: "M", color: { dark: "#0B1020", light: "#FFFFFF" } });

  const doc = new jsPDF({ unit: "mm", format: "a4" });
  const W = 210;
  const m = 16;
  const band = bandFor(c.batch.riskScore);
  const bandRgb: Record<string, [number, number, number]> = { green: [0, 150, 130], amber: [196, 120, 0], red: [214, 40, 90] };

  // header band
  doc.setFillColor(11, 16, 32);
  doc.rect(0, 0, W, 44, "F");
  doc.setFillColor(0, 194, 168);
  doc.rect(0, 44, W, 1.6, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(24);
  doc.text("EmbedProof certificate", m, 22);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(10.5);
  doc.text("Tamper-evident CO2 intensity record  |  ECDSA + ML-DSA-44 (FIPS 204)", m, 31);
  doc.setFontSize(9);
  doc.text("Hackathon prototype - demo data - not accredited CBAM verification", m, 38);

  // SEE
  doc.setTextColor(11, 16, 32);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.text("DECLARED SPECIFIC EMBEDDED EMISSIONS", m, 60);
  doc.setFontSize(46);
  doc.text(formatSee(c.batch.seeDeclared), m, 80);
  doc.setFontSize(12);
  doc.setFont("helvetica", "normal");
  doc.text("tCO2 per tonne of product", m + 62, 80);

  const [r, g, b] = bandRgb[band];
  doc.setFillColor(r, g, b);
  doc.roundedRect(W - m - 52, 56, 52, 12, 6, 6, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.text(`${band.toUpperCase()}  |  risk ${c.batch.riskScore}/100`, W - m - 26, 63.6, { align: "center" });

  // facts
  const rows: [string, string][] = [
    ["Installation", `${c.batch.installation}${c.installationName ? `  (${c.installationName})` : ""}`],
    ["Period", uint32ToPeriod(c.batch.period)],
    ["Batch", `#${c.batch.batchId}  -  ${c.batch.statusName}  -  ${formatTonnes(c.batch.verifiedTonnes)} attested tonnes`],
    ["Allocated to", `${c.importerLabel ?? c.allocation.importer}  -  ${formatTonnes(c.allocation.tonnes)} t`],
    ["Importer address", c.allocation.importer],
    ["Consignment", `${bytes32ToPrintable(c.allocation.consignmentId) ?? c.allocation.consignmentId}  -  CN ${c.allocation.cnCode}`],
    ["Chain", c.chainLabel],
    ["Commitment (keccak-256)", c.batch.commitment],
    ["Bundle CID", c.cid],
    ["ML-DSA signature hash", c.batch.pqSigHash],
    ["ML-DSA public key hash", c.pqKeyHash ?? "not available"],
    ["ML-DSA key anchored on-chain", c.pqAnchored === undefined ? "not checked" : c.pqAnchored ? "yes, the registry lists this key for the attestor" : "NO, the registry does not list this key"],
    ["Model version (SHA-256)", c.batch.modelVersionHash],
  ];
  if (c.txHash) rows.push(["Allocation tx", c.txHash]);
  let y = 98;
  doc.setTextColor(11, 16, 32);
  for (const [k, v] of rows) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(8.5);
    doc.setTextColor(90, 98, 120);
    doc.text(k.toUpperCase(), m, y);
    doc.setFont(v.startsWith("0x") || v.startsWith("bafk") ? "courier" : "helvetica", "normal");
    doc.setFontSize(v.length > 60 ? 7.5 : 10.5);
    doc.setTextColor(11, 16, 32);
    const lines = doc.splitTextToSize(v, W - 2 * m - 48) as string[];
    doc.text(lines, m + 48, y);
    y += Math.max(7.5, lines.length * 4.6 + 2.5);
  }

  if (c.explanation) {
    y += 2;
    doc.setFont("helvetica", "italic");
    doc.setFontSize(9.5);
    doc.setTextColor(60, 68, 90);
    const lines = doc.splitTextToSize(`AI note: ${c.explanation}`, W - 2 * m) as string[];
    doc.text(lines, m, y);
    y += lines.length * 4.6 + 4;
  }

  // QR
  const qy = Math.max(y + 4, 214);
  doc.setFillColor(244, 246, 250);
  doc.roundedRect(m, qy, W - 2 * m, 56, 4, 4, "F");
  doc.addImage(qr, "PNG", m + 4, qy + 4, 48, 48);
  doc.setTextColor(11, 16, 32);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(13);
  doc.text("Scan to verify - no wallet, no login", m + 58, qy + 14);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9.5);
  const how = doc.splitTextToSize(
    "Your browser re-computes the report fingerprint, checks the ECDSA and the post-quantum ML-DSA-44 signature against the chain, and lists every holder of this batch. Change one digit of the report and the checks fail. The QR carries the decryption key: share this page only with people who may see the report.",
    W - 2 * m - 64,
  ) as string[];
  doc.text(how, m + 58, qy + 22);
  doc.setFontSize(7);
  doc.setTextColor(60, 68, 90);
  doc.setFont("courier", "normal");
  const url = doc.splitTextToSize(c.verifyUrl, W - 2 * m - 64) as string[];
  doc.text(url.slice(0, 3), m + 58, qy + 47);

  return doc;
}

/** Browser-only: build the certificate and hand it to the user as a file download. */
export async function downloadCertificatePdf(c: CertificateInput): Promise<void> {
  const doc = await buildCertificatePdf(c);
  doc.save(`EmbedProof-certificate-batch-${c.batch.batchId}-${bytes32ToPrintable(c.allocation.consignmentId) ?? "consignment"}.pdf`);
}
