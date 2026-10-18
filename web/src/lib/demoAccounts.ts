/**
 * PUBLIC Anvil/Foundry test keys (mnemonic "test test ... junk"). They are known to the whole
 * internet - never fund them on a real network. This module is only reachable through a dynamic
 * import guarded by NEXT_PUBLIC_DEMO_MODE=1 (see signer.tsx), so a production build with demo
 * mode off does not ship these strings.
 */
import type { Address, Hex } from "viem";

export type PersonaKey = "admin" | "exporterA" | "exporterB" | "verifier" | "importerA" | "importerB" | "regulator";

export interface Persona {
  key: PersonaKey;
  anvilIndex: number;
  label: string;
  role: string;
  address: Address;
  privateKey: Hex;
}

export const DEMO_PERSONAS: Persona[] = [
  { key: "admin", anvilIndex: 0, label: "Admin", role: "Platform admin", address: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266", privateKey: "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80" },
  { key: "exporterA", anvilIndex: 2, label: "Exporter A", role: "Marmara Demir (EAF)", address: "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC", privateKey: "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a" },
  { key: "exporterB", anvilIndex: 3, label: "Exporter B", role: "Anadolu Entegre (BF-BOF)", address: "0x90F79bf6EB2c4f870365E785982E1f101E93b906", privateKey: "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6" },
  { key: "verifier", anvilIndex: 4, label: "Verifier", role: "Accredited verifier", address: "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65", privateKey: "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a" },
  { key: "importerA", anvilIndex: 5, label: "Importer A", role: "EU importer", address: "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc", privateKey: "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba" },
  { key: "importerB", anvilIndex: 6, label: "Importer B", role: "EU importer", address: "0x976EA74026E726554dB657fA54763abd0C3a0aa9", privateKey: "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e" },
  { key: "regulator", anvilIndex: 7, label: "Regulator", role: "Climate directorate", address: "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955", privateKey: "0x4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356" },
];
