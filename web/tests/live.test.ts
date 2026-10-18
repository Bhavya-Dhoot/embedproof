/**
 * Opt-in live checks against a running stack (skipped unless LIVE_RPC is set):
 *   LIVE_RPC=http://127.0.0.1:8545 pnpm test tests/live.test.ts
 * Needs `make up` (or an equivalent Anvil + deployed contracts with at least one allocated batch).
 * It pauses and unpauses the registry once (state is restored) and never sends an allocation.
 */
import { describe, expect, it } from "vitest";
import { createPublicClient, createWalletClient, http, type Abi, type Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { foundry } from "viem/chains";
import { DEMO_PERSONAS } from "../src/lib/demoAccounts";
import { allocatorAbi, attestationAbi, getDeployment, registryAbi, withExtraErrors } from "../src/lib/deployments";
import { decodeError } from "../src/lib/errors";
import { ROLE } from "../src/lib/chainReads";
import { stringToBytes32 } from "../src/lib/report";
import { ATTESTATION_TYPES, attestationDomain, attestationMessage } from "../src/lib/verify";
import { hashTypedData } from "viem";

const RPC = process.env.LIVE_RPC;
const dep = getDeployment();

describe.skipIf(!RPC || !dep)("live stack", () => {
  const pc = createPublicClient({ chain: foundry, transport: http(RPC) });
  const persona = (k: string) => DEMO_PERSONAS.find((p) => p.key === k)!;
  const wallet = (k: string) => createWalletClient({ chain: foundry, transport: http(RPC), account: privateKeyToAccount(persona(k).privateKey) });
  const sim = async (who: string, address: Hex, abi: Abi, functionName: string, args: unknown[] = []) => {
    try {
      await pc.simulateContract({ account: persona(who).address, address, abi: withExtraErrors(abi), functionName, args } as never);
      return null;
    } catch (e) {
      return decodeError(e);
    }
  };

  it("role hashes derived in the UI equal the contract's role constants", async () => {
    const read = (fn: string) => pc.readContract({ address: dep!.registry, abi: registryAbi, functionName: fn } as never);
    expect(await read("OPERATOR_ROLE")).toBe(ROLE.OPERATOR);
    expect(await read("VERIFIER_ROLE")).toBe(ROLE.VERIFIER);
    expect(await read("ATTESTOR_ROLE")).toBe(ROLE.ATTESTOR);
    expect(await read("REGULATOR_ROLE")).toBe(ROLE.REGULATOR);
    const has = (role: Hex, k: string) => pc.readContract({ address: dep!.registry, abi: registryAbi, functionName: "hasRole", args: [role, persona(k).address] });
    expect(await has(ROLE.VERIFIER, "verifier")).toBe(true);
    expect(await has(ROLE.REGULATOR, "regulator")).toBe(true);
    expect(await has(ROLE.ADMIN, "admin")).toBe(true);
    expect(await has(ROLE.VERIFIER, "importerA")).toBe(false);
  });

  it("decodes custom errors and OpenZeppelin errors to name + args", async () => {
    // wrong role
    const notAdmin = await sim("importerA", dep!.registry, registryAbi, "pause");
    expect(notAdmin?.name).toBe("AccessControlUnauthorizedAccount");

    // pause -> EnforcedPause on a state-changing call -> unpause
    await pc.waitForTransactionReceipt({ hash: await wallet("admin").writeContract({ address: dep!.registry, abi: registryAbi, functionName: "pause" }) });
    try {
      const paused = await sim("exporterA", dep!.allocator, allocatorAbi, "allocate", [1n, stringToBytes32("LIVE-TEST-1"), persona("importerA").address, 72083900, 1n]);
      expect(paused?.name).toBe("EnforcedPause");
    } finally {
      await pc.waitForTransactionReceipt({ hash: await wallet("admin").writeContract({ address: dep!.registry, abi: registryAbi, functionName: "unpause" }) });
    }

    // unknown batch
    const unknown = await sim("exporterA", dep!.attestation, attestationAbi, "getBatch", [999999n]);
    expect(unknown?.name).toBe("UnknownBatch");
  });

  it("a second claim on a fully allocated batch decodes to DoubleClaimBlocked(batchId, requested, remaining)", async () => {
    const count = await pc.readContract({ address: dep!.attestation, abi: attestationAbi, functionName: "batchCount" });
    if (count === 0n) return; // empty chain: nothing to check yet (run the demo scenarios first)
    const remaining = await pc.readContract({ address: dep!.allocator, abi: allocatorAbi, functionName: "remainingTonnes", args: [1n] });
    const b = await pc.readContract({ address: dep!.attestation, abi: attestationAbi, functionName: "getBatch", args: [1n] });
    if (remaining !== 0n || b.status !== 2) return; // only meaningful once batch 1 is Attested and fully allocated (the demo runner leaves it so)
    const e = await sim("exporterA", dep!.allocator, allocatorAbi, "allocate", [1n, stringToBytes32("LIVE-TEST-2"), persona("importerB").address, 72083900, b.verifiedTonnes]);
    expect(e?.name).toBe("DoubleClaimBlocked");
    expect(e?.args).toEqual([1n, b.verifiedTonnes, 0n]);
  });

  it("hashAttestation(AttestInput) on the deployed proxy equals viem's EIP-712 digest (domain = this chain + the proxy)", async () => {
    const hasFn = (attestationAbi as Abi).some((i) => i.type === "function" && i.name === "hashAttestation");
    if (!hasFn) return; // contracts predate INTERFACES 9A
    const a = {
      installationId: stringToBytes32("TR-EAF-0001"), period: 202609, commitment: ("0x" + "11".repeat(32)) as Hex, seeDeclared: 4190, verifiedTonnes: 5000n,
      riskScore: 12, modelVersionHash: ("0x" + "22".repeat(32)) as Hex, algId: 1, pqSigHash: ("0x" + "33".repeat(32)) as Hex,
      cidDigest: ("0x" + "44".repeat(32)) as Hex, ecdsaSig: "0x" as Hex,
    };
    const onChainDigest = await pc.readContract({ address: dep!.attestation, abi: attestationAbi, functionName: "hashAttestation", args: [a] } as never);
    const batchLike = { ...a, commitment: a.commitment, cidDigest: a.cidDigest, attestor: persona("admin").address };
    const message = attestationMessage(batchLike, a.commitment);
    const ours = hashTypedData({ domain: attestationDomain(31337, dep!.attestation), types: ATTESTATION_TYPES, primaryType: "Attestation", message });
    expect(onChainDigest).toBe(ours);
  });
});
