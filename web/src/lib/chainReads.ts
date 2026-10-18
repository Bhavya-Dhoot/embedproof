import { keccak256, toHex, zeroAddress, type Abi, type Address, type Hex } from "viem";
import type { AppPublicClient } from "./client";
import { allocatorAbi, attestationAbi, registryAbi, type Deployment } from "./deployments";
import { bytes2ToCountry, bytes32ToString, routeEnumToName, SECTORS, STATUS, type RouteName, type StatusName } from "./report";

export const ROLE = {
  ADMIN: ("0x" + "00".repeat(32)) as Hex,
  OPERATOR: keccak256(toHex("OPERATOR_ROLE")),
  VERIFIER: keccak256(toHex("VERIFIER_ROLE")),
  ATTESTOR: keccak256(toHex("ATTESTOR_ROLE")),
  REGULATOR: keccak256(toHex("REGULATOR_ROLE")),
};

export interface InstallationView {
  id: Hex;
  idStr: string;
  country: string;
  sector: (typeof SECTORS)[number];
  route: RouteName;
  routeEnum: number;
  capacityTpy: bigint;
  operator: Address;
}

export interface BatchView {
  batchId: bigint;
  installationId: Hex;
  installation: string;
  period: number;
  seeDeclared: number;
  verifiedTonnes: bigint;
  riskScore: number;
  algId: number;
  status: number;
  statusName: StatusName;
  attestedAt: number;
  commitment: Hex;
  modelVersionHash: Hex;
  pqSigHash: Hex;
  cidDigest: Hex;
  attestor: Address;
  verifier: Address;
  reasonHash: Hex;
}

export interface AllocationView {
  batchId: bigint;
  consignmentId: Hex;
  importer: Address;
  cnCode: number;
  tonnes: bigint;
  claimed: boolean;
  txHash?: Hex;
  blockNumber?: bigint;
}

export interface RequestView {
  id: Hex;
  idStr: string;
  requester: Address;
  country: string;
  sector: (typeof SECTORS)[number];
  route: RouteName;
  routeEnum: number;
  capacityTpy: bigint;
  txHash: Hex;
}

export interface DecisionView {
  batchId: bigint;
  verifier: Address;
  approved: boolean;
  reasonHash: Hex;
  txHash: Hex;
  blockNumber: bigint;
}

type AnyArgs = Record<string, unknown>;
interface LogLite {
  args: AnyArgs;
  transactionHash: Hex;
  blockNumber: bigint;
  logIndex: number;
}

const CHUNK = 9_000n;

/** getLogs in block windows so public RPCs with range limits still work. */
async function getEvents(pc: AppPublicClient, address: Address, abi: Abi, eventName: string, args: AnyArgs | undefined, fromBlock: number): Promise<LogLite[]> {
  const latest = await pc.getBlockNumber();
  const out: LogLite[] = [];
  for (let from = BigInt(fromBlock); from <= latest; from += CHUNK) {
    const to = from + CHUNK - 1n > latest ? latest : from + CHUNK - 1n;
    const logs = await pc.getContractEvents({ address, abi, eventName, args, fromBlock: from, toBlock: to });
    for (const l of logs as unknown as { args?: AnyArgs; transactionHash: Hex; blockNumber: bigint; logIndex: number }[]) {
      out.push({ args: l.args ?? {}, transactionHash: l.transactionHash, blockNumber: l.blockNumber, logIndex: l.logIndex });
    }
  }
  return out;
}

// ---------------- registry ----------------

export async function readInstallations(pc: AppPublicClient, dep: Deployment): Promise<InstallationView[]> {
  const ids = (await pc.readContract({ address: dep.registry, abi: registryAbi, functionName: "installationIds" })) as readonly Hex[];
  const items = await Promise.all(
    ids.map((id) => pc.readContract({ address: dep.registry, abi: registryAbi, functionName: "getInstallation", args: [id] })),
  );
  return items.map((i) => ({
    id: i.id,
    idStr: bytes32ToString(i.id),
    country: bytes2ToCountry(i.country),
    sector: SECTORS[i.sector] ?? "STEEL",
    route: routeEnumToName(i.route),
    routeEnum: i.route,
    capacityTpy: i.capacityTpy,
    operator: i.operator,
  }));
}

export interface Roles {
  admin: boolean;
  verifier: boolean;
  regulator: boolean;
  operatorOf: string[];
  /** true once we have read the chain at least once */
  known: boolean;
}

export const NO_ROLES: Roles = { admin: false, verifier: false, regulator: false, operatorOf: [], known: false };

export async function readRoles(pc: AppPublicClient, dep: Deployment, who: Address, installations: InstallationView[]): Promise<Roles> {
  const has = (role: Hex) => pc.readContract({ address: dep.registry, abi: registryAbi, functionName: "hasRole", args: [role, who] });
  const [admin, verifier, regulator] = await Promise.all([has(ROLE.ADMIN), has(ROLE.VERIFIER), has(ROLE.REGULATOR)]);
  return {
    admin,
    verifier,
    regulator,
    operatorOf: installations.filter((i) => i.operator.toLowerCase() === who.toLowerCase()).map((i) => i.idStr),
    known: true,
  };
}

/** keccak256 of the attestor's anchored ML-DSA-44 public key (0x00..00 if none); null if the registry has no such function. */
export async function readAttestorPqKey(pc: AppPublicClient, dep: Deployment, attestor: Address): Promise<Hex | null> {
  try {
    return await pc.readContract({ address: dep.registry, abi: registryAbi, functionName: "attestorPqKey", args: [attestor] });
  } catch {
    return null;
  }
}

/** registry.isAttestorPqKey(attestor, hash): true for every key ever anchored and not revoked (INTERFACES 9E). Falls back to the current key on a registry that predates it. */
export async function readIsAttestorPqKey(pc: AppPublicClient, dep: Deployment, attestor: Address, keyHash: Hex): Promise<boolean> {
  try {
    return await pc.readContract({ address: dep.registry, abi: registryAbi, functionName: "isAttestorPqKey", args: [attestor, keyHash] });
  } catch {
    const current = await pc.readContract({ address: dep.registry, abi: registryAbi, functionName: "attestorPqKey", args: [attestor] });
    return !/^0x0+$/.test(current) && current.toLowerCase() === keyHash.toLowerCase();
  }
}

export interface RevocationView {
  by: Address;
  reasonHash: Hex;
  txHash: Hex;
}

/** The BatchRevoked event for a batch (admin revocation, INTERFACES 9C), or null. */
export async function readBatchRevocation(pc: AppPublicClient, dep: Deployment, batchId: bigint): Promise<RevocationView | null> {
  try {
    const logs = await getEvents(pc, dep.attestation, attestationAbi, "BatchRevoked", { batchId }, dep.deployBlock);
    const last = logs[logs.length - 1];
    if (!last) return null;
    const a = last.args as { by: Address; reasonHash: Hex };
    return { by: a.by, reasonHash: a.reasonHash, txHash: last.transactionHash };
  } catch {
    return null;
  }
}

export async function readPaused(pc: AppPublicClient, dep: Deployment): Promise<boolean> {
  return pc.readContract({ address: dep.registry, abi: registryAbi, functionName: "paused" });
}

export async function readRequests(pc: AppPublicClient, dep: Deployment): Promise<RequestView[]> {
  const logs = await getEvents(pc, dep.registry, registryAbi, "InstallationRequested", undefined, dep.deployBlock);
  return logs.map((l) => {
    const a = l.args as { id: Hex; requester: Address; country: Hex; sector: number; route: number; capacityTpy: bigint };
    return {
      id: a.id,
      idStr: bytes32ToString(a.id),
      requester: a.requester,
      country: bytes2ToCountry(a.country),
      sector: SECTORS[a.sector] ?? "STEEL",
      route: routeEnumToName(a.route),
      routeEnum: a.route,
      capacityTpy: a.capacityTpy,
      txHash: l.transactionHash,
    };
  });
}

// ---------------- attestation ----------------

export async function readBatch(pc: AppPublicClient, dep: Deployment, batchId: bigint): Promise<BatchView> {
  const b = await pc.readContract({ address: dep.attestation, abi: attestationAbi, functionName: "getBatch", args: [batchId] });
  return {
    batchId,
    installationId: b.installationId,
    installation: bytes32ToString(b.installationId),
    period: b.period,
    seeDeclared: b.seeDeclared,
    verifiedTonnes: b.verifiedTonnes,
    riskScore: b.riskScore,
    algId: b.algId,
    status: b.status,
    statusName: STATUS[b.status] ?? "None",
    attestedAt: b.attestedAt,
    commitment: b.commitment,
    modelVersionHash: b.modelVersionHash,
    pqSigHash: b.pqSigHash,
    cidDigest: b.cidDigest,
    attestor: b.attestor,
    verifier: b.verifier,
    reasonHash: b.reasonHash,
  };
}

export async function readBatches(pc: AppPublicClient, dep: Deployment): Promise<BatchView[]> {
  const count = await pc.readContract({ address: dep.attestation, abi: attestationAbi, functionName: "batchCount" });
  const n = Number(count);
  if (n === 0) return [];
  const ids = Array.from({ length: n }, (_, i) => BigInt(i + 1));
  return Promise.all(ids.map((id) => readBatch(pc, dep, id)));
}

export async function readDecisions(pc: AppPublicClient, dep: Deployment): Promise<DecisionView[]> {
  const logs = await getEvents(pc, dep.attestation, attestationAbi, "VerifierDecision", undefined, dep.deployBlock);
  return logs.map((l) => {
    const a = l.args as { batchId: bigint; verifier: Address; approved: boolean; reasonHash: Hex };
    return { batchId: a.batchId, verifier: a.verifier, approved: a.approved, reasonHash: a.reasonHash, txHash: l.transactionHash, blockNumber: l.blockNumber };
  });
}

export async function readModelApproved(pc: AppPublicClient, dep: Deployment, hash: Hex): Promise<boolean> {
  return pc.readContract({ address: dep.attestation, abi: attestationAbi, functionName: "isModelApproved", args: [hash] });
}

export async function readRiskThreshold(pc: AppPublicClient, dep: Deployment): Promise<number> {
  return pc.readContract({ address: dep.attestation, abi: attestationAbi, functionName: "riskThreshold" });
}

// ---------------- allocator ----------------

export async function readRemaining(pc: AppPublicClient, dep: Deployment, batchId: bigint): Promise<bigint> {
  return pc.readContract({ address: dep.allocator, abi: allocatorAbi, functionName: "remainingTonnes", args: [batchId] });
}

async function hydrateAllocations(pc: AppPublicClient, dep: Deployment, logs: LogLite[]): Promise<AllocationView[]> {
  const uniq = [...new Map(logs.map((l) => [(l.args.consignmentId as Hex).toLowerCase(), l])).values()];
  return Promise.all(
    uniq.map(async (l) => {
      const a = l.args as { batchId: bigint; consignmentId: Hex; importer: Address; cnCode: number; tonnes: bigint };
      const live = await pc.readContract({ address: dep.allocator, abi: allocatorAbi, functionName: "getAllocation", args: [a.consignmentId] });
      return {
        batchId: a.batchId,
        consignmentId: a.consignmentId,
        importer: live.importer === zeroAddress ? a.importer : live.importer,
        cnCode: live.cnCode,
        tonnes: live.tonnes,
        claimed: live.claimed,
        txHash: l.transactionHash,
        blockNumber: l.blockNumber,
      };
    }),
  );
}

export async function readAllocationsForBatch(pc: AppPublicClient, dep: Deployment, batchId: bigint): Promise<AllocationView[]> {
  const logs = await getEvents(pc, dep.allocator, allocatorAbi, "Allocated", { batchId }, dep.deployBlock);
  return hydrateAllocations(pc, dep, logs);
}

export async function readAllocationsForImporter(pc: AppPublicClient, dep: Deployment, importer: Address): Promise<AllocationView[]> {
  const logs = await getEvents(pc, dep.allocator, allocatorAbi, "Allocated", { importer }, dep.deployBlock);
  return hydrateAllocations(pc, dep, logs);
}

export async function readAllAllocations(pc: AppPublicClient, dep: Deployment): Promise<AllocationView[]> {
  const logs = await getEvents(pc, dep.allocator, allocatorAbi, "Allocated", undefined, dep.deployBlock);
  return hydrateAllocations(pc, dep, logs);
}
