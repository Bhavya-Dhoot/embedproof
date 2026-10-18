import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { hashDomain, hashTypedData, recoverTypedDataAddress, type Address, type Hex, type TypedDataDomain } from "viem";
import { ATTESTATION_TYPES, attestationDomain, attestationMessage, type OnchainBatch } from "../src/lib/verify";

// Golden vector written by the contracts build: the Solidity hashAttestation digest, cross-checked there against eth_account.
const path = fileURLToPath(new URL("../../contracts/test/vectors/eip712_vector.json", import.meta.url));

interface ContractVector {
  domain: { name: string; version: string; chainId: number; verifyingContract: Address };
  primaryType: "Attestation";
  types: { Attestation: { name: string; type: string }[] };
  message: { installationId: Hex; period: number; commitment: Hex; seeDeclared: number; verifiedTonnes: number; riskScore: number; modelVersionHash: Hex; algId: number; pqSigHash: Hex };
  domainSeparator: Hex;
  structHash: Hex;
  digest: Hex;
  signer: Address;
  signature: Hex;
}

describe.skipIf(!existsSync(path))("contracts/test/vectors/eip712_vector.json", () => {
  const v = (existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : {}) as ContractVector;
  const domain = (): TypedDataDomain => attestationDomain(v.domain.chainId, v.domain.verifyingContract);
  const onchain = (): OnchainBatch => ({
    commitment: v.message.commitment,
    attestor: v.signer,
    pqSigHash: v.message.pqSigHash,
    cidDigest: ("0x" + "00".repeat(32)) as Hex,
    installationId: v.message.installationId,
    period: v.message.period,
    seeDeclared: v.message.seeDeclared,
    verifiedTonnes: BigInt(v.message.verifiedTonnes),
    riskScore: v.message.riskScore,
    modelVersionHash: v.message.modelVersionHash,
    algId: v.message.algId,
  });

  it("our Attestation type list is field for field the contract's", () => {
    expect(ATTESTATION_TYPES.Attestation.map((f) => `${f.type} ${f.name}`)).toEqual(v.types.Attestation.map((f) => `${f.type} ${f.name}`));
  });

  it("domain separator and digest equal the Solidity values, and the signature recovers the signer", async () => {
    expect(domain()).toEqual(v.domain);
    expect(hashDomain({ domain: { name: v.domain.name, version: v.domain.version, chainId: BigInt(v.domain.chainId), verifyingContract: v.domain.verifyingContract }, types: { EIP712Domain: [{ name: "name", type: "string" }, { name: "version", type: "string" }, { name: "chainId", type: "uint256" }, { name: "verifyingContract", type: "address" }] } })).toBe(v.domainSeparator);
    const message = attestationMessage(onchain(), v.message.commitment);
    expect(hashTypedData({ domain: domain(), types: ATTESTATION_TYPES, primaryType: "Attestation", message })).toBe(v.digest);
    expect(await recoverTypedDataAddress({ domain: domain(), types: ATTESTATION_TYPES, primaryType: "Attestation", message, signature: v.signature })).toBe(v.signer);
  });
});
