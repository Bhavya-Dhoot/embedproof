"use client";

import { useEffect, useState } from "react";
import { AllocateFlow } from "@/components/exporter/AllocateFlow";
import { AttestFlow } from "@/components/exporter/AttestFlow";
import { Installations } from "@/components/exporter/Installations";
import { NeedsDeployment, PageHeader, cx } from "@/components/ui";

const TABS = [
  { id: "attest", label: "1 · Attest", blurb: "Score and sign a monthly report" },
  { id: "allocate", label: "2 · Allocate", blurb: "Assign attested tonnes to importers" },
  { id: "installations", label: "Installations", blurb: "Registry and onboarding" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export default function ExporterPage() {
  const [tab, setTab] = useState<Tab>("attest");
  const [preselect, setPreselect] = useState<string | undefined>();

  useEffect(() => {
    const h = window.location.hash.replace("#", "");
    if (TABS.some((t) => t.id === h)) setTab(h as Tab);
  }, []);

  const go = (t: Tab) => {
    setTab(t);
    window.history.replaceState(null, "", `#${t}`);
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Exporter" title="Attest the claim, then allocate the tonnes">
        Submit a monthly report, see how the AI scores it and why, sign it, and allocate the attested tonnes to importers.
      </PageHeader>

      <div role="tablist" aria-label="Exporter workflow" className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            onClick={() => go(t.id)}
            className={cx("btn", tab === t.id ? "btn-primary" : "btn-ghost")}
          >
            {t.label}
          </button>
        ))}
      </div>

      <NeedsDeployment>
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "attest" && (
            <AttestFlow
              onAllocate={(id) => {
                setPreselect(id);
                go("allocate");
              }}
            />
          )}
          {tab === "allocate" && <AllocateFlow preselect={preselect} />}
          {tab === "installations" && <Installations />}
        </div>
      </NeedsDeployment>
    </div>
  );
}
