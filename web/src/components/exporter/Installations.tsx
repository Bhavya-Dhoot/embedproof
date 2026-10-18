"use client";

import { useState } from "react";
import { ActAs, Chip, Field, Glass, Notice, Spinner, TxLink, cx } from "@/components/ui";
import { getDeployment, registryAbi } from "@/lib/deployments";
import { INSTALLATION_NAMES, labelAddress } from "@/lib/demoMeta";
import { decodeError, formatArgs, type DecodedError } from "@/lib/errors";
import { useInstallations, useRefreshChain, useRequests } from "@/lib/hooks";
import { ROUTES, SECTORS, countryToBytes2, formatTonnes, shortAddr, stringToBytes32 } from "@/lib/report";
import { useSigner, type TxOutcome } from "@/lib/signer";

export function Installations() {
  const signer = useSigner();
  const dep = getDeployment();
  const refresh = useRefreshChain();
  const inst = useInstallations();
  const reqs = useRequests();

  const [id, setId] = useState("TR-NEW-0003");
  const [country, setCountry] = useState("TR");
  const [sector, setSector] = useState(0);
  const [route, setRoute] = useState(1);
  const [cap, setCap] = useState("250000");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<DecodedError | null>(null);
  const [ok, setOk] = useState<TxOutcome | null>(null);

  const valid = (() => {
    try {
      stringToBytes32(id);
      countryToBytes2(country);
      return Number.isInteger(Number(cap)) && Number(cap) > 0;
    } catch {
      return false;
    }
  })();

  const request = async () => {
    if (!dep) return;
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      setOk(
        await signer.write({
          address: dep.registry,
          abi: registryAbi,
          functionName: "requestInstallation",
          args: [stringToBytes32(id), countryToBytes2(country), sector, route, BigInt(cap)],
        }),
      );
      refresh();
    } catch (e) {
      setErr(decodeError(e));
    } finally {
      setBusy(false);
    }
  };

  const registered = new Set((inst.data ?? []).map((i) => i.idStr));
  const mineRequests = (reqs.data ?? []).filter((r) => signer.address && r.requester.toLowerCase() === signer.address.toLowerCase());

  return (
    <div className="flex flex-col gap-5">
      <Glass>
        <h2 className="mb-1 text-2xl font-bold">Registered installations</h2>
        <p className="mb-4 text-sm text-[var(--color-mute)]">The registry is the trust anchor: a report is only accepted if it is signed by the installation&apos;s registered operator.</p>
        {inst.isLoading ? (
          <Spinner />
        ) : (inst.data ?? []).length === 0 ? (
          <Notice tone="neutral" title="No installations registered yet">Request one below; an admin approves it.</Notice>
        ) : (
          <div className="scroll-x">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Installation</th>
                  <th>Route</th>
                  <th>Country</th>
                  <th className="text-right">Capacity t/yr</th>
                  <th>Operator</th>
                </tr>
              </thead>
              <tbody>
                {inst.data!.map((i) => {
                  const mine = !!signer.address && i.operator.toLowerCase() === signer.address.toLowerCase();
                  return (
                    <tr key={i.id}>
                      <td>
                        <span className="font-bold">{i.idStr}</span>
                        {INSTALLATION_NAMES[i.idStr] && <span className="block text-xs text-white/75">{INSTALLATION_NAMES[i.idStr]}</span>}
                      </td>
                      <td>
                        <Chip>{i.route}</Chip>
                      </td>
                      <td>{i.country}</td>
                      <td className="num text-right">{formatTonnes(i.capacityTpy)}</td>
                      <td>
                        {labelAddress(i.operator) ?? shortAddr(i.operator)} {mine && <Chip tone="green">you</Chip>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Glass>

      <Glass>
        <h2 className="mb-1 text-2xl font-bold">Request onboarding</h2>
        <p className="mb-4 text-sm text-[var(--color-mute)]">Anyone can ask. The request is an on-chain event; an admin registers it and makes the requester its operator.</p>
        <ActAs keys={["exporterA", "exporterB", "admin"]} reason="Connect or choose an account to send the request from." />
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="Installation id" htmlFor="r-id" hint="≤ 32 ASCII chars">
            <input id="r-id" className="inp mono" value={id} maxLength={32} onChange={(e) => setId(e.target.value)} />
          </Field>
          <Field label="Country" htmlFor="r-cc" hint="ISO-2">
            <input id="r-cc" className="inp" value={country} maxLength={2} onChange={(e) => setCountry(e.target.value.toUpperCase())} />
          </Field>
          <Field label="Sector" htmlFor="r-sec">
            <select id="r-sec" className="inp" value={sector} onChange={(e) => setSector(Number(e.target.value))}>
              {SECTORS.map((s, i) => (
                <option key={s} value={i}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Production route" htmlFor="r-route">
            <select id="r-route" className="inp" value={route} onChange={(e) => setRoute(Number(e.target.value))}>
              {ROUTES.map((s, i) => (
                <option key={s} value={i}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Capacity (t/yr)" htmlFor="r-cap">
            <input id="r-cap" className="inp" inputMode="numeric" value={cap} onChange={(e) => setCap(e.target.value.replace(/[^\d]/g, ""))} />
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button className="btn btn-primary" onClick={request} disabled={!valid || busy || !signer.address}>
            {busy ? <Spinner label="Requesting…" /> : "Request onboarding"}
          </button>
          {!signer.address && <span className="hint">Choose a persona first.</span>}
        </div>
        <div className="mt-4 flex flex-col gap-3">
          {err && (
            <Notice tone="red" title={err.userRejected ? "Signature rejected" : `Reverted: ${err.name}`}>
              {err.message}
              {err.args.length > 0 && <span className="mono block">args: {formatArgs(err.args)}</span>}
            </Notice>
          )}
          {ok && (
            <Notice tone="green" title="Request sent">
              <TxLink hash={ok.hash} />
              <span className="mt-1 block">An admin can now register it from the Admin view.</span>
            </Notice>
          )}
          {mineRequests.length > 0 && (
            <ul className="m-0 list-none p-0 text-sm">
              {mineRequests.map((r) => (
                <li key={r.txHash} className={cx("flex items-center gap-2 py-1")}>
                  <span className="font-bold">{r.idStr}</span> {r.route} · {r.country} · {formatTonnes(r.capacityTpy)} t/yr
                  {registered.has(r.idStr) ? <Chip tone="green">registered</Chip> : <Chip tone="amber">pending admin</Chip>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </Glass>
    </div>
  );
}
