"use client";

import { use, useEffect, useState } from "react";
import { api, apiPost, screenshotUrl } from "@/lib/api";

type ExtractionDetail = {
  extraction_id: string;
  snapshot_id: string;
  status: string;
  model: string | null;
  company: string;
  url: string;
  extraction: Record<string, unknown>;
};

const FORMATS = ["html_table", "js", "pdf", "image", "unknown"];

export default function ReviewEditor({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [detail, setDetail] = useState<ExtractionDetail | null>(null);
  const [text, setText] = useState("");
  const [fmt, setFmt] = useState("html_table");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);

  useEffect(() => {
    api<ExtractionDetail>(`/v1/review/extractions/${id}`).then((d) => {
      setDetail(d);
      setText(JSON.stringify(d.extraction, null, 2));
    }).catch((e) => setMsg({ kind: "err", text: String(e) }));
  }, [id]);

  async function save() {
    if (!detail) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setMsg({ kind: "err", text: `Invalid JSON: ${e}` });
      return;
    }
    try {
      const res = await apiPost<{ slug: string }>("/v1/review/golden", {
        snapshot_id: detail.snapshot_id,
        corrected: parsed,
        format: fmt,
      });
      setMsg({ kind: "ok", text: `Saved labeled fixture "${res.slug}". It now counts toward the eval gate.` });
    } catch (e) {
      setMsg({ kind: "err", text: String(e) });
    }
  }

  if (!detail) return <p className="muted">Loading…</p>;

  return (
    <div>
      <h1>Review: {detail.company}</h1>
      <p className="muted small">
        Model: {detail.model} · status: {detail.status} · <a href={detail.url} target="_blank" rel="noreferrer">source page</a>
      </p>
      <div className="notice warn">
        This is the model&apos;s extraction — a <strong>draft</strong>. Correct it against the screenshot, then save. Saving marks it labeled (verified) and it becomes eval ground truth.
      </div>

      <div className="filters">
        <label>Format:&nbsp;
          <select value={fmt} onChange={(e) => setFmt(e.target.value)}>
            {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </label>
        <button onClick={save}>Save as labeled golden</button>
      </div>
      {msg && <div className={`notice ${msg.kind}`}>{msg.text}</div>}

      <div className="grid2" style={{ alignItems: "start" }}>
        <div>
          <h2>Extraction (editable)</h2>
          <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        </div>
        <div>
          <h2>Source screenshot</h2>
          <div style={{ maxHeight: "80vh", overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
            <img className="shot" src={screenshotUrl(detail.snapshot_id)} alt="source page screenshot" />
          </div>
        </div>
      </div>
    </div>
  );
}
