"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { isAddress, isHex, type Address, type Hex } from "viem";
import { ActAs, Chip, Field, Glass, Mono, NeedsDeployment, Notice, PageHeader, Spinner, TxLink } from "@/components/ui";
import { readAttestorPqKey, readIsAttestorPqKey, readModelApproved, readRiskThreshold } from "@/lib/chainReads";
import { publicClient } from "@/lib/client";
import { attestationAbi, getDeployment, registryAbi } from "@/lib/deployments";
import { decodeError, formatArgs, type DecodedError } from "@/lib/errors";
import { attestorApi } from "@/lib/attestor";
import { useAttestorHealth, useBatches, useChainStatus, useInstallations, useRefreshChain, useRequests, useRoles } from "@/lib/hooks";
import { SECTORS, ROUTES, countryToBytes2, formatSee, formatTonnes, shortAddr, stringToBytes32, uint32ToPeriod } from "@/lib/report";
import { labelAddress } from "@/lib/demoMeta";
import { useSigner, type TxOutcome, type WriteParams } from "@/lib/signer";

function useAction() {
  const signer = useSigner();
  const refresh = useRefreshChain();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<DecodedError | null>(null);
  const [ok, setOk] = useState<{ what: string; tx: TxOutcome } | null>(null);
  const run = async (what: string, p: WriteParams | (() => Promise<WriteParams>)) => {
    setBusy(what);
    setErr(null);
    setOk(null);
    try {
      setOk({ what, tx: await signer.write(typeof p === "function" ? await p() : p) });
      refresh();
    } catch (e) {
      setErr(decodeError(e));
    } finally {
      setBusy(null);
    }
  };
  return { busy, err, ok, run };
}

export default function AdminPage() {
  const dep = getDeployment();
  const roles = useRoles();
  const chain = useChainStatus();
  const health = useAttestorHealth();
  const reqs = useRequests();
  const inst = useInstallations();
  const accounts = dep?.accounts as Record<string, string | undefined> | undefined;
  const { busy, err, ok, run } = useAction();
  const batches = useBatches();
  const [revokeId, setRevokeId] = useState("");
  const [revokeReason, setRevokeReason] = useState("");
  const [opId, setOpId] = useState("");
  const [opAddr, setOpAddr] = useState("");
  const [modelHash, setModelHash] = useState("");
  const hash = (modelHash || health.data?.modelVersionHash || dep?.modelVersionHash || "") as string;
  const hashOk = isHex(hash) && hash.length === 66;

  const approved = useQuery({
    queryKey: ["chain", "modelApproved", hash],
    enabled: !!dep && hashOk,
    refetchInterval: 4000,
    queryFn: () => readModelApproved(publicClient, dep!, hash as Hex),
  });
  // post-quantum key anchor: registry.attestorPqKey(attestor) vs the key hash the attestor reports
  const attestorAddr = (health.data?.attestor ?? dep?.accounts.attestor) as Address | undefined;
  const livePq = health.data?.pqPublicKeyHash;
  const anchored = useQuery({
    queryKey: ["chain", "attestorPqKey", attestorAddr],
    enabled: !!dep && !!attestorAddr && isAddress(attestorAddr),
    refetchInterval: 4000,
    queryFn: () => readAttestorPqKey(publicClient, dep!, attestorAddr!),
  });
  // history-aware: the attestor's key counts as anchored if registry.isAttestorPqKey says so (it stays true after a rotation)
  const livePqAnchored = useQuery({
    queryKey: ["chain", "isAttestorPqKey", attestorAddr, livePq],
    enabled: !!dep && !!attestorAddr && isAddress(attestorAddr) && !!livePq,
    refetchInterval: 4000,
    queryFn: () => readIsAttestorPqKey(publicClient, dep!, attestorAddr!, livePq as Hex),
  });
  const pqZero = !anchored.data || /^0x0+$/.test(anchored.data);
  const pqMatches = !!livePqAnchored.data;
  const threshold = useQuery({ queryKey: ["chain", "threshold"], enabled: !!dep, queryFn: () => readRiskThreshold(publicClient, dep!) });

  const registered = new Set((inst.data ?? []).map((i) => i.idStr));
  const pending = (reqs.data ?? []).filter((r) => !registered.has(r.idStr));
  const paused = chain.data?.paused ?? false;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Admin" title="Keys to the registry">
        Register installations from onboarding requests, approve a frozen model version, and pull the emergency brake. Upgrades are not here on purpose: they only go through the 24-hour timelock.
      </PageHeader>
      <ActAs role="admin" />
      <NeedsDeployment>
        <div className="grid gap-6 lg:grid-cols-2">
          <Glass>
            <h2 className="mb-1 text-2xl font-bold">System</h2>
            <div className="mb-4 flex flex-wrap gap-2">
              <Chip tone={paused ? "amber" : "green"}>{paused ? "paused" : "running"}</Chip>
              <Chip>risk threshold {threshold.data ?? "n/a"}</Chip>
            </div>
            <p className="mb-4 text-sm text-[var(--color-mute)]">While paused, attest, verifierSign, allocate and claim revert with EnforcedPause().</p>
            <div className="flex flex-wrap gap-3">
              <button className="btn btn-danger" disabled={!roles.admin || paused || !!busy} onClick={() => run("pause", { address: dep!.registry, abi: registryAbi, functionName: "pause" })}>
                {busy === "pause" ? <Spinner label="Pausing…" /> : "Pause system"}
              </button>
              <button className="btn btn-primary" disabled={!roles.admin || !paused || !!busy} onClick={() => run("unpause", { address: dep!.registry, abi: registryAbi, functionName: "unpause" })}>
                {busy === "unpause" ? <Spinner label="Resuming…" /> : "Unpause"}
              </button>
            </div>
          </Glass>

          <Glass>
            <h2 className="mb-1 text-2xl font-bold">Model version</h2>
            <p className="mb-3 text-sm text-[var(--color-mute)]">The attestor refuses to attest with a model hash that is not approved on-chain. Revoking a version makes later attests revert with ModelNotApproved (existing batches are untouched).</p>
            <Field label="Model version hash (sha-256 of the frozen artefact)" htmlFor="m-hash" hint={health.data ? "Prefilled from the attestor's /health" : undefined}>
              <input id="m-hash" className="inp mono" value={hash} onChange={(e) => setModelHash(e.target.value.trim())} placeholder="0x…" />
            </Field>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              {hashOk && approved.data !== undefined && <Chip tone={approved.data ? "green" : "amber"}>{approved.data ? "approved" : "not approved"}</Chip>}
              <button className="btn btn-primary" disabled={!roles.admin || !hashOk || !!approved.data || !!busy} onClick={() => run("approve", { address: dep!.attestation, abi: attestationAbi, functionName: "approveModel", args: [hash as Hex] })}>
                {busy === "approve" ? <Spinner label="Approving…" /> : "Approve model"}
              </button>
              <button className="btn btn-danger" disabled={!roles.admin || !hashOk || approved.data !== true || !!busy} onClick={() => run("revoke", { address: dep!.attestation, abi: attestationAbi, functionName: "revokeModel", args: [hash as Hex] })}>
                {busy === "revoke" ? <Spinner label="Revoking…" /> : "Revoke model"}
              </button>
            </div>
          </Glass>
        </div>

        <Glass>
          <h2 className="mb-1 text-2xl font-bold">Post-quantum key anchor</h2>
          <p className="mb-4 text-sm text-[var(--color-mute)]">
            The registry pins keccak256 of the attestor&apos;s ML-DSA-44 public key. Every public certificate page checks that the key inside a bundle is this one, so nobody can pass off a bundle signed with a key of their own.
          </p>
          <dl className="m-0 mb-4 grid gap-4 sm:grid-cols-3">
            <div>
              <dt className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Attestor</dt>
              <dd className="m-0 mt-1">{attestorAddr ? <Mono value={attestorAddr} /> : <span className="hint">unknown</span>}</dd>
            </div>
            <div>
              <dt className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Anchored on-chain</dt>
              <dd className="m-0 mt-1">
                {anchored.isLoading ? <Spinner /> : anchored.data === null || anchored.data === undefined ? <span className="hint">registry has no attestorPqKey</span> : pqZero ? <Chip tone="amber">not anchored</Chip> : <Mono value={anchored.data} />}
              </dd>
            </div>
            <div>
              <dt className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">Attestor reports</dt>
              <dd className="m-0 mt-1">{livePq ? <Mono value={livePq} /> : <span className="hint">attestor offline or older build</span>}</dd>
            </div>
          </dl>
          <div className="flex flex-wrap items-center gap-3">
            {pqMatches ? <Chip tone="green">key anchored on-chain</Chip> : !pqZero && livePq ? <Chip tone="red">current key differs</Chip> : null}
            <button
              className="btn btn-primary"
              disabled={!roles.admin || !livePq || !attestorAddr || pqMatches || !!busy}
              onClick={() => run("anchor", { address: dep!.registry, abi: registryAbi, functionName: "setAttestorPqKey", args: [attestorAddr!, livePq as Hex] })}
            >
              {busy === "anchor" ? <Spinner label="Anchoring…" /> : pqZero ? "Anchor attestor's ML-DSA key" : "Replace anchored key"}
            </button>
          </div>
        </Glass>

        <div className="grid gap-6 lg:grid-cols-2">
          <Glass>
            <h2 className="mb-1 text-2xl font-bold">Revoke a batch</h2>
            <p className="mb-4 text-sm text-[var(--color-mute)]">
              Moves a Pending or Attested batch to Rejected for good. Its tonnes can no longer be allocated, and the public certificate shows who revoked it and why. A written reason is required.
            </p>
            <Field label="Batch" htmlFor="rv-batch">
              <select id="rv-batch" className="inp" value={revokeId} onChange={(e) => setRevokeId(e.target.value)}>
                <option value="">Choose a batch</option>
                {(batches.data ?? [])
                  .filter((b) => b.statusName === "Attested" || b.statusName === "PendingVerifier")
                  .map((b) => (
                    <option key={b.batchId.toString()} value={b.batchId.toString()}>
                      #{b.batchId.toString()} · {b.installation} · {uint32ToPeriod(b.period)} · SEE {formatSee(b.seeDeclared)} · {b.statusName}
                    </option>
                  ))}
              </select>
            </Field>
            <div className="mt-3">
              <Field label="Reason (stored off-chain, its hash goes on-chain)" htmlFor="rv-reason">
                <textarea id="rv-reason" className="inp" maxLength={1000} value={revokeReason} onChange={(e) => setRevokeReason(e.target.value)} placeholder="e.g. Site visit found the electric arc furnace was idle in this period." />
              </Field>
            </div>
            <div className="mt-3">
              <button
                className="btn btn-danger"
                disabled={!roles.admin || !revokeId || !revokeReason.trim() || !!busy}
                onClick={() =>
                  run("revoke-batch", async () => {
                    const { reasonHash } = await attestorApi.postReason(revokeId, revokeReason.trim());
                    return { address: dep!.attestation, abi: attestationAbi, functionName: "revokeBatch", args: [BigInt(revokeId), reasonHash] };
                  }).then(() => setRevokeReason(""))
                }
              >
                {busy === "revoke-batch" ? <Spinner label="Revoking…" /> : "Revoke batch"}
              </button>
            </div>
          </Glass>

          <Glass>
            <h2 className="mb-1 text-2xl font-bold">Change an operator</h2>
            <p className="mb-4 text-sm text-[var(--color-mute)]">
              Only the registered operator of an installation can submit reports and allocate its tonnes. Setting a new one also grants it the OPERATOR role.
            </p>
            <Field label="Installation" htmlFor="op-inst">
              <select id="op-inst" className="inp" value={opId} onChange={(e) => setOpId(e.target.value)}>
                <option value="">Choose an installation</option>
                {(inst.data ?? []).map((i) => (
                  <option key={i.id} value={i.idStr}>
                    {i.idStr} · now {labelAddress(i.operator, accounts) ?? shortAddr(i.operator)}
                  </option>
                ))}
              </select>
            </Field>
            <div className="mt-3">
              <Field label="New operator address" htmlFor="op-addr">
                <input id="op-addr" className="inp mono" value={opAddr} onChange={(e) => setOpAddr(e.target.value.trim())} placeholder="0x..." aria-invalid={!!opAddr && !isAddress(opAddr)} />
              </Field>
              <div className="mt-2 flex flex-wrap gap-2">
                {(["exporterA", "exporterB"] as const)
                  .map((k) => ({ k, a: accounts?.[k] }))
                  .filter((x) => !!x.a)
                  .map((x) => (
                    <button key={x.k} type="button" className="btn btn-ghost btn-sm" onClick={() => setOpAddr(x.a!)}>
                      {x.k === "exporterA" ? "Exporter A" : "Exporter B"}
                    </button>
                  ))}
              </div>
            </div>
            <div className="mt-3">
              <button
                className="btn btn-primary"
                disabled={!roles.admin || !opId || !isAddress(opAddr) || !!busy}
                onClick={() => run("operator", { address: dep!.registry, abi: registryAbi, functionName: "setOperator", args: [stringToBytes32(opId), opAddr as Address] })}
              >
                {busy === "operator" ? <Spinner label="Updating…" /> : "Set operator"}
              </button>
            </div>
          </Glass>
        </div>

        <Glass>
          <h2 className="mb-1 text-2xl font-bold">Onboarding requests</h2>
          <p className="mb-4 text-sm text-[var(--color-mute)]">From <span className="mono">InstallationRequested</span> events. Registering makes the requester the installation&apos;s operator and grants OPERATOR_ROLE.</p>
          {reqs.isLoading ? (
            <Spinner />
          ) : pending.length === 0 ? (
            <p className="text-[var(--color-mute)]">No pending requests.</p>
          ) : (
            <div className="scroll-x">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Installation</th>
                    <th>Route</th>
                    <th>Country</th>
                    <th className="text-right">Capacity</th>
                    <th>Requester</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {pending.map((r) => (
                    <tr key={r.txHash}>
                      <td className="font-bold">{r.idStr}</td>
                      <td>{ROUTES[r.routeEnum]} <span className="text-white/75">({SECTORS[SECTORS.indexOf(r.sector)]})</span></td>
                      <td>{r.country}</td>
                      <td className="num text-right">{formatTonnes(r.capacityTpy)}</td>
                      <td>{labelAddress(r.requester, accounts) ?? shortAddr(r.requester)}</td>
                      <td>
                        <button
                          className="btn btn-primary btn-sm"
                          disabled={!roles.admin || !!busy}
                          onClick={() =>
                            run(`register-${r.idStr}`, {
                              address: dep!.registry,
                              abi: registryAbi,
                              functionName: "registerInstallation",
                              args: [stringToBytes32(r.idStr), countryToBytes2(r.country), SECTORS.indexOf(r.sector), r.routeEnum, r.capacityTpy, r.requester],
                            })
                          }
                        >
                          {busy === `register-${r.idStr}` ? <Spinner label="Registering…" /> : "Register"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Glass>

        {err && (
          <Notice tone="red" title={err.userRejected ? "Signature rejected" : `Reverted: ${err.name}`}>
            {err.message}
            {err.args.length > 0 && <span className="mono block">args: {formatArgs(err.args)}</span>}
          </Notice>
        )}
        {ok && (
          <Notice tone="green" title={`Done: ${ok.what.split("-")[0]}`}>
            <TxLink hash={ok.tx.hash} />
          </Notice>
        )}
        {dep && (
          <Glass>
            <h2 className="mb-3 text-xl font-bold">Deployment</h2>
            <dl className="m-0 grid gap-3 sm:grid-cols-2">
              {(
                [
                  ["Registry", dep.registry],
                  ["Attestation", dep.attestation],
                  ["Allocator", dep.allocator],
                  ["Timelock (upgrade authority)", dep.timelock],
                ] as const
              ).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-[0.7rem] font-bold uppercase tracking-[0.14em] text-white/75">{k}</dt>
                  <dd className="m-0 mt-1"><Mono value={v} /></dd>
                </div>
              ))}
            </dl>
          </Glass>
        )}
      </NeedsDeployment>
    </div>
  );
}
