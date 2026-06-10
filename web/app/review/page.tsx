"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

type ExtractionRow = {
  extraction_id: string;
  snapshot_id: string;
  status: string;
  extracted_at: string;
  company: string;
};

export default function ReviewListPage() {
  const [rows, setRows] = useState<ExtractionRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<ExtractionRow[]>("/v1/review/extractions").then(setRows).catch((e) => setErr(String(e)));
  }, []);

  return (
    <div>
      <h1>Review &amp; label</h1>
      <p className="muted">
        Correct an extraction against its source screenshot, then save it as a labeled
        golden fixture. Labeled fixtures are what the eval gate scores against.
      </p>
      {err && <div className="notice err">{err}</div>}
      <table>
        <thead><tr><th>Company</th><th>Extraction status</th><th>Extracted</th><th></th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.extraction_id}>
              <td>{r.company}</td>
              <td><span className={`badge ${r.status === "needs_review" ? "" : "gray"}`}>{r.status}</span></td>
              <td className="small muted">{new Date(r.extracted_at).toISOString().slice(0, 16).replace("T", " ")}</td>
              <td><Link href={`/review/${r.extraction_id}`}>Review →</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
