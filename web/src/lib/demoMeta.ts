import type { Address } from "viem";
import type { Report } from "./report";

/** docs/INTERFACES.md section 2 - fictional companies. Display names only; the chain stores ids. */
export const INSTALLATION_NAMES: Record<string, string> = {
  "TR-EAF-0001": "Marmara Demir Mini-Mill (fictional)",
  "TR-BOF-0002": "Anadolu Entegre Çelik (fictional)",
};

/** docs/INTERFACES.md section 1 - public addresses, used for importer quick-picks if no deployment file. */
export const FALLBACK_IMPORTERS: { label: string; address: Address }[] = [
  { label: "Importer A", address: "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc" },
  { label: "Importer B", address: "0x976EA74026E726554dB657fA54763abd0C3a0aa9" },
];

/** Built-in copies of the shipped demo reports (data/demo_reports/*.json, which override these when present). */
export const FALLBACK_DEMO_REPORTS: Record<"honest_eaf" | "implausible_bof", Report> = {
  honest_eaf: {
    schema: "embedproof.report.v1",
    installationId: "TR-EAF-0001",
    period: "2026-09",
    route: "EAF",
    cnCode: "72142000",
    productionTonnes: 5000,
    directEmissionsTco2: 1200.0,
    electricityMwh: 3100.0,
    gridFactorTco2PerMwh: 0.476,
    indirectEmissionsTco2: 1475.6,
    seeTco2PerT: 0.535,
  },
  implausible_bof: {
    schema: "embedproof.report.v1",
    installationId: "TR-BOF-0002",
    period: "2026-09",
    route: "BF-BOF",
    cnCode: "72083900",
    productionTonnes: 250000,
    directEmissionsTco2: 90000.0,
    electricityMwh: 105000.0,
    gridFactorTco2PerMwh: 0.476,
    indirectEmissionsTco2: 49980.0,
    seeTco2PerT: 0.56,
  },
};

/** docs/INTERFACES.md section 1 public addresses -> friendly names (lower-case keys). */
export const ADDRESS_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries({
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266": "Admin",
    "0x70997970C51812dc3A010C7d01b50e0d17dc79C8": "Attestor",
    "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC": "Exporter A",
    "0x90F79bf6EB2c4f870365E785982E1f101E93b906": "Exporter B",
    "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65": "Verifier",
    "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc": "Importer A",
    "0x976EA74026E726554dB657fA54763abd0C3a0aa9": "Importer B",
    "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955": "Regulator",
  }).map(([a, l]) => [a.toLowerCase(), l]),
);

export function labelAddress(addr: string, accounts?: Record<string, string | undefined>): string | undefined {
  const a = addr.toLowerCase();
  if (accounts) {
    const names: Record<string, string> = { admin: "Admin", attestor: "Attestor", exporterA: "Exporter A", exporterB: "Exporter B", verifier: "Verifier", importerA: "Importer A", importerB: "Importer B", regulator: "Regulator" };
    for (const [k, v] of Object.entries(accounts)) if (v && v.toLowerCase() === a && names[k]) return names[k];
  }
  return ADDRESS_LABELS[a];
}
