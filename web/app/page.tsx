"use client";

import { useEffect, useState } from "react";
import { api, type CompanyRow, type Facets, type ProgramSearch } from "@/lib/api";
import { ProgramTable } from "./components";

export default function ExplorePage() {
  const [facets, setFacets] = useState<Facets | null>(null);
  const [companies, setCompanies] = useState<CompanyRow[]>([]);
  const [q, setQ] = useState("");
  const [phase, setPhase] = useState("");
  const [modality, setModality] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [therapeuticArea, setTherapeuticArea] = useState("");
  const [status, setStatus] = useState("");
  const [includeDiscontinued, setIncludeDiscontinued] = useState(false);
  const [data, setData] = useState<ProgramSearch | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<Facets>("/v1/facets").then(setFacets).catch((e) => setErr(String(e)));
    api<CompanyRow[]>("/v1/companies").then((c) => setCompanies(c.filter((x) => x.n_programs > 0))).catch(() => {});
  }, []);

  async function run() {
    setLoading(true);
    setErr(null);
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (phase) params.set("phase", phase);
    if (modality) params.set("modality", modality);
    if (companyId) params.set("company_id", companyId);
    if (therapeuticArea) params.set("therapeutic_area", therapeuticArea);
    if (status) params.set("status", status);
    if (!includeDiscontinued) params.set("active_only", "true");
    params.set("limit", "200");
    try {
      setData(await api<ProgramSearch>(`/v1/programs?${params.toString()}`));
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <h1>Explore pipelines</h1>
      <p className="muted">Faceted search across company-disclosed drug pipelines. Every row links to its source.</p>

      <div className="filters">
        <input
          type="search"
          placeholder="Search asset, indication, or target…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <select value={therapeuticArea} onChange={(e) => setTherapeuticArea(e.target.value)}>
          <option value="">All therapeutic areas</option>
          {facets?.therapeutic_areas.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={phase} onChange={(e) => setPhase(e.target.value)}>
          <option value="">All phases</option>
          {facets?.phases.map((p) => <option key={p.code} value={p.code}>{p.label}</option>)}
        </select>
        <select value={modality} onChange={(e) => setModality(e.target.value)}>
          <option value="">All modalities</option>
          {facets?.modalities.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={companyId} onChange={(e) => setCompanyId(e.target.value)}>
          <option value="">All companies</option>
          {companies.map((c) => <option key={c.company_id} value={c.company_id}>{c.name} ({c.n_programs})</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {facets?.statuses.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <label className="small" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={includeDiscontinued} onChange={(e) => setIncludeDiscontinued(e.target.checked)} style={{ minWidth: "auto" }} />
          Include discontinued/removed
        </label>
        <button onClick={run}>Search</button>
        <button className="ghost" onClick={() => { setQ(""); setPhase(""); setModality(""); setCompanyId(""); setTherapeuticArea(""); setStatus(""); setIncludeDiscontinued(false); }}>Clear</button>
      </div>

      {err && <div className="notice err">{err}</div>}
      {loading ? <p className="muted">Loading…</p> : data && (
        <>
          <p className="muted small">{data.total} program{data.total === 1 ? "" : "s"}{data.total > data.results.length ? ` (showing ${data.results.length})` : ""}</p>
          <ProgramTable rows={data.results} />
        </>
      )}
    </div>
  );
}
