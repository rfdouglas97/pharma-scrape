"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import type { ProgramRow } from "@/lib/api";
import { api } from "@/lib/api";
import { ProgramTable } from "../../components";

type CompanyDetail = {
  company_id: string;
  name: string;
  ticker: string | null;
  country: string | null;
  website: string | null;
  n_programs: number;
  n_discontinued: number;
  programs_by_phase: Record<string, ProgramRow[]>;
  discontinued: ProgramRow[];
};

const PHASE_ORDER = ["approved", "filed", "phase_3", "phase_2_3", "phase_2", "phase_1_2", "phase_1", "preclinical", "discontinued", "unmapped"];

export default function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [c, setC] = useState<CompanyDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<CompanyDetail>(`/v1/companies/${id}`).then(setC).catch((e) => setErr(String(e)));
  }, [id]);

  if (err) return <div className="notice err">{err}</div>;
  if (!c) return <p className="muted">Loading…</p>;

  const phases = Object.keys(c.programs_by_phase).sort(
    (a, b) => PHASE_ORDER.indexOf(a) - PHASE_ORDER.indexOf(b)
  );

  return (
    <div>
      <h1>{c.name}{c.ticker ? <span className="muted"> · {c.ticker}</span> : null}</h1>
      <p className="muted">
        {c.n_programs} active programs
        {c.n_discontinued ? ` · ${c.n_discontinued} discontinued/removed` : ""}
        {c.website ? <> · <a href={c.website} target="_blank" rel="noreferrer">{c.website}</a></> : null}
        {" · "}<Link href={`/companies/${id}/history`}>Pipeline history over time ↗</Link>
      </p>
      {phases.map((ph) => (
        <div key={ph}>
          <h2>{ph.replace(/_/g, " ")} <span className="muted small">({c.programs_by_phase[ph].length})</span></h2>
          <ProgramTable rows={c.programs_by_phase[ph]} />
        </div>
      ))}
      {c.discontinued.length > 0 && (
        <div>
          <h2>Discontinued / removed <span className="muted small">({c.discontinued.length})</span></h2>
          <ProgramTable rows={c.discontinued} />
        </div>
      )}
    </div>
  );
}
