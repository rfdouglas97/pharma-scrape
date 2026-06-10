"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type CompanyRow } from "@/lib/api";

export default function CompaniesPage() {
  const [rows, setRows] = useState<CompanyRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<CompanyRow[]>("/v1/companies").then(setRows).catch((e) => setErr(String(e)));
  }, []);

  return (
    <div>
      <h1>Companies</h1>
      <p className="muted">Registry coverage and freshness. Companies with 0 programs are seeded but not yet ingested/loaded.</p>
      {err && <div className="notice err">{err}</div>}
      <table>
        <thead>
          <tr><th>Company</th><th>Ticker</th><th>Country</th><th>Active programs</th><th>Discontinued</th><th>Last fetched</th></tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.company_id}>
              <td>{c.n_programs > 0 ? <Link href={`/companies/${c.company_id}`}>{c.name}</Link> : <span>{c.name}</span>}</td>
              <td className="small">{c.ticker || "—"}</td>
              <td className="small">{c.country || "—"}</td>
              <td>{c.n_programs > 0 ? <span className="badge">{c.n_programs}</span> : <span className="badge gray">0</span>}</td>
              <td>{c.n_discontinued > 0 ? <span className="badge discontinued">{c.n_discontinued}</span> : <span className="muted small">—</span>}</td>
              <td className="small muted">{c.last_fetched ? new Date(c.last_fetched).toISOString().slice(0, 10) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
