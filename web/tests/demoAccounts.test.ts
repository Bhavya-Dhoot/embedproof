import { describe, expect, it } from "vitest";
import { privateKeyToAccount } from "viem/accounts";
import { DEMO_PERSONAS } from "../src/lib/demoAccounts";

// docs/INTERFACES.md section 1
const EXPECTED: Record<number, string> = {
  0: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
  2: "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
  3: "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
  4: "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
  5: "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",
  6: "0x976EA74026E726554dB657fA54763abd0C3a0aa9",
  7: "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955",
};

describe("demo personas", () => {
  it("every public Anvil test key derives the INTERFACES.md section 1 address", () => {
    expect(DEMO_PERSONAS.map((p) => p.anvilIndex)).toEqual([0, 2, 3, 4, 5, 6, 7]);
    for (const p of DEMO_PERSONAS) {
      expect(privateKeyToAccount(p.privateKey).address, p.label).toBe(EXPECTED[p.anvilIndex]);
      expect(p.address, p.label).toBe(EXPECTED[p.anvilIndex]);
    }
  });
});

import { isAddress } from "viem";
import { ADDRESS_LABELS, FALLBACK_IMPORTERS } from "../src/lib/demoMeta";

describe("address labels", () => {
  it("every labelled address is a valid 20-byte address and matches the personas", () => {
    for (const a of Object.keys(ADDRESS_LABELS)) expect(isAddress(a, { strict: false }), a).toBe(true);
    for (const p of DEMO_PERSONAS) expect(ADDRESS_LABELS[p.address.toLowerCase()], p.label).toBe(p.label);
    for (const i of FALLBACK_IMPORTERS) expect(ADDRESS_LABELS[i.address.toLowerCase()], i.label).toBe(i.label);
  });
});
