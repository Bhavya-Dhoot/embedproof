"use client";

import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Address } from "viem";
import { attestorApi } from "./attestor";
import {
  NO_ROLES,
  readAllAllocations,
  readAllocationsForBatch,
  readAllocationsForImporter,
  readBatches,
  readDecisions,
  readInstallations,
  readPaused,
  readRequests,
  readRoles,
  type Roles,
} from "./chainReads";
import { publicClient } from "./client";
import { attestationAbi, getDeployment } from "./deployments";
import { CHAIN_ID } from "./env";
import { holdsRole, ROLE_LABEL, roleMismatchMessage, type ViewRole } from "./roles";
import { useSigner } from "./signer";

const POLL = CHAIN_ID === 31337 ? 3000 : 8000;

export function useDeployment() {
  return useMemo(() => getDeployment(), []);
}

/** After any confirmed tx: refresh every chain-derived query. */
export function useRefreshChain() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["chain"] });
}

export function useChainStatus() {
  const dep = useDeployment();
  return useQuery({
    queryKey: ["chain", "status", dep?.registry],
    refetchInterval: POLL,
    retry: false,
    queryFn: async () => {
      const block = await publicClient.getBlockNumber();
      let paused = false;
      let batchCount = 0;
      if (dep) {
        paused = await readPaused(publicClient, dep).catch(() => false);
        batchCount = Number(
          await publicClient.readContract({ address: dep.attestation, abi: attestationAbi, functionName: "batchCount" }).catch(() => 0n),
        );
      }
      return { block, paused, batchCount };
    },
  });
}

export function useAttestorHealth() {
  return useQuery({ queryKey: ["attestor", "health"], queryFn: attestorApi.health, refetchInterval: 10_000, retry: false });
}

export function useInstallations() {
  const dep = useDeployment();
  return useQuery({
    queryKey: ["chain", "installations", dep?.registry],
    enabled: !!dep,
    refetchInterval: POLL,
    queryFn: () => readInstallations(publicClient, dep!),
  });
}

export function useBatches() {
  const dep = useDeployment();
  return useQuery({
    queryKey: ["chain", "batches", dep?.attestation],
    enabled: !!dep,
    refetchInterval: POLL,
    queryFn: () => readBatches(publicClient, dep!),
  });
}

export function useDecisions() {
  const dep = useDeployment();
  return useQuery({
    queryKey: ["chain", "decisions", dep?.attestation],
    enabled: !!dep,
    refetchInterval: POLL,
    queryFn: () => readDecisions(publicClient, dep!),
  });
}

export function useRequests() {
  const dep = useDeployment();
  return useQuery({
    queryKey: ["chain", "requests", dep?.registry],
    enabled: !!dep,
    refetchInterval: POLL,
    queryFn: () => readRequests(publicClient, dep!),
  });
}

export function useAllocationsForBatch(batchId: bigint | undefined) {
  const dep = useDeployment();
  return useQuery({
    queryKey: ["chain", "allocations", "batch", batchId?.toString()],
    enabled: !!dep && batchId !== undefined,
    refetchInterval: POLL,
    queryFn: () => readAllocationsForBatch(publicClient, dep!, batchId!),
  });
}

export function useAllocationsForImporter(importer: Address | undefined) {
  const dep = useDeployment();
  return useQuery({
    queryKey: ["chain", "allocations", "importer", importer],
    enabled: !!dep && !!importer,
    refetchInterval: POLL,
    queryFn: () => readAllocationsForImporter(publicClient, dep!, importer!),
  });
}

export function useAllAllocations() {
  const dep = useDeployment();
  return useQuery({
    queryKey: ["chain", "allocations", "all"],
    enabled: !!dep,
    refetchInterval: POLL,
    queryFn: () => readAllAllocations(publicClient, dep!),
  });
}

/** Roles of the connected signer, read from the registry (FR-F1). */
export function useRoles(): Roles & { address?: Address; loading: boolean } {
  const dep = useDeployment();
  const { address } = useSigner();
  const inst = useInstallations();
  const q = useQuery({
    queryKey: ["chain", "roles", address, inst.data?.map((i) => i.operator).join(",")],
    enabled: !!dep && !!address && !!inst.data,
    refetchInterval: POLL,
    queryFn: () => readRoles(publicClient, dep!, address!, inst.data!),
  });
  return { ...(q.data ?? NO_ROLES), address, loading: q.isLoading };
}

/** Role gate for a page: `allowed` is false until an account is chosen and its roles are read, and for accounts that lack the role. */
export function useRoleGate(need: ViewRole): { allowed: boolean; message: string | null; needLabel: string } {
  const roles = useRoles();
  return {
    allowed: !!roles.address && roles.known && holdsRole(roles, need),
    message: roles.address ? roleMismatchMessage(roles, need) : null,
    needLabel: ROLE_LABEL[need],
  };
}
