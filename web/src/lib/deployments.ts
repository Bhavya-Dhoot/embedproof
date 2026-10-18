import { isAddress, type Abi, type Address, type Hex } from "viem";
import { abis, demoReports, deployments } from "@/generated/manifest";
import { CHAIN_ID } from "./env";
import { allocatorAbiFallback, attestationAbiFallback, extraErrorsAbi, registryAbiFallback } from "./abi";
import { FALLBACK_DEMO_REPORTS } from "./demoMeta";
import type { Report } from "./report";

export interface Deployment {
  chainId: number;
  rpcUrl?: string;
  deployBlock: number;
  timelock: Address;
  registry: Address;
  attestation: Address;
  allocator: Address;
  modelVersionHash?: Hex;
  accounts: Partial<Record<"admin" | "attestor" | "exporterA" | "exporterB" | "verifier" | "importerA" | "importerB" | "regulator", Address>>;
}

/** The deployment for NEXT_PUBLIC_CHAIN_ID, or null when `make demo` has not produced one yet. */
export function getDeployment(chainId: number = CHAIN_ID): Deployment | null {
  const raw = deployments[String(chainId)] as Partial<Deployment> | undefined;
  if (!raw) return null;
  if (!raw.registry || !raw.attestation || !raw.allocator) return null;
  if (![raw.registry, raw.attestation, raw.allocator].every((a) => isAddress(a as string))) return null;
  return {
    chainId: raw.chainId ?? chainId,
    rpcUrl: raw.rpcUrl,
    deployBlock: Number(raw.deployBlock ?? 0),
    timelock: raw.timelock as Address,
    registry: raw.registry,
    attestation: raw.attestation,
    allocator: raw.allocator,
    modelVersionHash: raw.modelVersionHash,
    accounts: raw.accounts ?? {},
  };
}

function unwrapAbi(x: unknown): Abi | null {
  if (Array.isArray(x)) return x as Abi;
  const inner = (x as { abi?: unknown } | undefined)?.abi;
  return Array.isArray(inner) ? (inner as Abi) : null;
}

/**
 * Prefer the generated ABI JSON; keep the compile-time type of the parseAbi mirror. Entries that the
 * mirror knows (INTERFACES.md) but a stale generated ABI lacks are appended, so the UI keeps working
 * between an interface update and the next `make` ABI export.
 */
function pick<T extends Abi>(name: string, fallback: T): T {
  const gen = unwrapAbi(abis[name]);
  if (!gen) return fallback;
  const have = new Set(gen.map((i) => `${i.type}:${"name" in i ? i.name : ""}`));
  const extra = (fallback as Abi).filter((i) => !have.has(`${i.type}:${"name" in i ? i.name : ""}`));
  return [...gen, ...extra] as unknown as T;
}

export const registryAbi = pick("InstallationRegistry", registryAbiFallback);
export const attestationAbi = pick("EmissionsAttestation", attestationAbiFallback);
export const allocatorAbi = pick("ConsignmentAllocator", allocatorAbiFallback);

/** ABI used to decode reverts for any of the three contracts (plus OpenZeppelin errors). */
export const allErrorsAbi = [...registryAbi, ...attestationAbi, ...allocatorAbi, ...extraErrorsAbi].filter((i) => i.type === "error") as Abi;

/** Contract ABI + OZ errors, so pause / access-control reverts decode even if the ABI JSON omits them. */
export function withExtraErrors<T extends Abi>(abi: T): T {
  const have = new Set(abi.filter((i) => i.type === "error").map((i) => (i as { name: string }).name));
  return [...abi, ...extraErrorsAbi.filter((e) => !have.has(e.name))] as unknown as T;
}

export function getDemoReports(): { honest: Report; implausible: Report; amber?: Report } {
  const g = demoReports as Record<string, Report | undefined>;
  return {
    honest: g["honest_eaf"] ?? FALLBACK_DEMO_REPORTS.honest_eaf,
    implausible: g["implausible_bof"] ?? FALLBACK_DEMO_REPORTS.implausible_bof,
    amber: g["amber_eaf"],
  };
}
