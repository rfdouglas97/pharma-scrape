import Link from "next/link";
import type { ProgramRow } from "@/lib/api";

export function PhaseBadge({ row }: { row: { phase_label?: string | null; phase_code?: string | null; phase_verbatim: string | null; status: string | null } }) {
  const cls = row.status === "discontinued" ? "badge discontinued" : "badge";
  // Show the NORMALIZED phase ("Phase 2"); fall back to verbatim if unmapped.
  // The raw page wording is preserved as the tooltip.
  const label = row.phase_label || row.phase_verbatim || "—";
  const title = row.phase_verbatim && row.phase_verbatim !== label ? `verbatim: ${row.phase_verbatim}` : undefined;
  return <span className={cls} title={title}>{label}</span>;
}

export function Provenance({ row }: { row: { source_url: string | null; fetched_at: string | null; snapshot_id: string | null } }) {
  if (!row.source_url) return <span className="muted small">—</span>;
  const date = row.fetched_at ? new Date(row.fetched_at).toISOString().slice(0, 10) : "";
  return (
    <span className="provenance">
      <a href={row.source_url} target="_blank" rel="noreferrer">source</a>
      {date && ` · ${date}`}
      {row.snapshot_id && (
        <>
          {" · "}
          <a href={`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/v1/snapshots/${row.snapshot_id}/screenshot`} target="_blank" rel="noreferrer">screenshot</a>
        </>
      )}
    </span>
  );
}

export function Moa({ row }: { row: ProgramRow }) {
  // Disclosed mechanism wins. Otherwise show the enriched target gene, marked as such.
  if (row.mechanism_verbatim) return <span>{row.mechanism_verbatim}</span>;
  if (row.target_symbols) {
    const ot = row.target_source === "open_targets";
    return (
      <span title={ot ? "Target gene backfilled from Open Targets (company disclosed no mechanism)" : undefined}>
        {row.target_symbols}{ot ? <span className="muted"> · OT</span> : null}
      </span>
    );
  }
  return <span className="muted">—</span>;
}

export function ProgramTable({ rows }: { rows: ProgramRow[] }) {
  if (!rows.length) return <p className="muted">No programs match.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Asset</th><th>Company</th><th>Indication</th><th>Mechanism / MoA</th><th>Therapeutic area</th><th>Phase</th><th>Provenance</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.program_id}>
            <td><Link href={`/assets/${r.asset_id}`}>{r.asset_name}</Link></td>
            <td><Link href={`/companies/${r.company_id}`}>{r.company_name}</Link>{r.ticker ? <span className="muted small"> ({r.ticker})</span> : null}</td>
            <td title={r.efo_label ? `mapped: ${r.efo_label}${r.efo_curie ? ` (${r.efo_curie})` : ""}` : undefined}>
              {r.indication_verbatim || r.indication}
            </td>
            <td className="small"><Moa row={r} /></td>
            <td className="small">{r.therapeutic_area ? <span className="badge gray">{r.therapeutic_area}</span> : <span className="muted">—</span>}</td>
            <td><PhaseBadge row={r} /></td>
            <td><Provenance row={r} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
