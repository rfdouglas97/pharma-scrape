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

export function ProgramTable({ rows }: { rows: ProgramRow[] }) {
  if (!rows.length) return <p className="muted">No programs match.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Asset</th><th>Company</th><th>Indication</th><th>Phase</th><th>Modality</th><th>Provenance</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.program_id}>
            <td><Link href={`/assets/${r.asset_id}`}>{r.asset_name}</Link></td>
            <td><Link href={`/companies/${r.company_id}`}>{r.company_name}</Link>{r.ticker ? <span className="muted small"> ({r.ticker})</span> : null}</td>
            <td>{r.indication_verbatim || r.indication}</td>
            <td><PhaseBadge row={r} /></td>
            <td className="small">{r.modality_verbatim || (r.modality_code ?? <span className="muted">—</span>)}</td>
            <td><Provenance row={r} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
