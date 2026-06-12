"use client";

import { Fragment, use, useEffect, useState } from "react";
import type { ProgramRow } from "@/lib/api";
import { api } from "@/lib/api";
import { ProgramTable } from "../../components";

type AssetDetail = {
  asset_id: string;
  preferred_name: string;
  modality_code: string | null;
  modality_verbatim: string | null;
  modality_source: string;
  mechanism_verbatim: string | null;
  chembl_id: string | null;
  extras: Record<string, string>;
  synonyms: { synonym: string; type: string }[];
  targets: { symbol: string | null; name: string | null; verbatim: string | null; action: string | null; source: string }[];
  partners: { name: string; role: string | null; territory: string | null }[];
  programs: ProgramRow[];
};

function SourceBadge({ source }: { source: string }) {
  if (source === "open_targets")
    return <span className="badge gray small" title="Backfilled from Open Targets, not company-disclosed">Open Targets</span>;
  return <span className="badge small" title="Disclosed on the company pipeline page">disclosed</span>;
}

export default function AssetPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [a, setA] = useState<AssetDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<AssetDetail>(`/v1/assets/${id}`).then(setA).catch((e) => setErr(String(e)));
  }, [id]);

  if (err) return <div className="notice err">{err}</div>;
  if (!a) return <p className="muted">Loading…</p>;

  const extras = Object.entries(a.extras || {});

  return (
    <div>
      <h1>{a.preferred_name}</h1>
      <div className="grid2">
        <div className="card">
          <dl className="kv">
            <dt>Mechanism / MoA</dt>
            <dd>
              {a.mechanism_verbatim ? (
                <>{a.mechanism_verbatim} <SourceBadge source="disclosed" /></>
              ) : <span className="muted">not disclosed</span>}
            </dd>
            <dt>Modality</dt>
            <dd>
              {a.modality_verbatim || a.modality_code ? (
                <>{a.modality_verbatim || a.modality_code} <SourceBadge source={a.modality_source} /></>
              ) : <span className="muted">not disclosed</span>}
            </dd>
            <dt>Target (gene)</dt>
            <dd>
              {a.targets.length ? a.targets.map((t, i) => (
                <span key={i} style={{ marginRight: 8 }}>
                  {t.symbol || t.verbatim || t.name} <SourceBadge source={t.source} />
                </span>
              )) : <span className="muted">{a.mechanism_verbatim ? "see Mechanism / MoA above" : "not disclosed"}</span>}
            </dd>
            <dt>Synonyms / codes</dt><dd>{a.synonyms.filter((sn) => sn.synonym !== a.preferred_name).map((sn) => sn.synonym).join(", ") || <span className="muted">—</span>}</dd>
            <dt>Partners</dt><dd>{a.partners.length ? a.partners.map((p) => p.name + (p.role ? ` (${p.role})` : "")).join(", ") : <span className="muted">—</span>}</dd>
            {a.chembl_id ? <><dt>ChEMBL</dt><dd className="small"><a href={`https://platform.opentargets.org/drug/${a.chembl_id}`} target="_blank" rel="noreferrer">{a.chembl_id}</a></dd></> : null}
          </dl>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Disclosed extras</h2>
          {extras.length ? (
            <dl className="kv">
              {extras.map(([k, v]) => (<Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>))}
            </dl>
          ) : <p className="muted">None captured.</p>}
        </div>
      </div>

      <h2>Programs ({a.programs.length})</h2>
      <ProgramTable rows={a.programs} />
    </div>
  );
}
