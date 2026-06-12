"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { HistDistribution, HistEvents, HistSummary } from "@/lib/api";
import { DistributionArea, FlowChart } from "./charts";

type Company = { name: string; ticker: string | null };

export default function HistoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [co, setCo] = useState<Company | null>(null);
  const [summary, setSummary] = useState<HistSummary | null>(null);
  const [phase, setPhase] = useState<HistDistribution | null>(null);
  const [ta, setTa] = useState<HistDistribution | null>(null);
  const [events, setEvents] = useState<HistEvents | null>(null);
  const [pct, setPct] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<Company>(`/v1/companies/${id}`).then(setCo).catch(() => {});
    api<HistSummary>(`/v1/companies/${id}/history/summary`).then(setSummary).catch((e) => setErr(String(e)));
    api<HistDistribution>(`/v1/companies/${id}/history/distribution?dim=phase`).then(setPhase).catch(() => {});
    api<HistDistribution>(`/v1/companies/${id}/history/distribution?dim=therapeutic_area`).then(setTa).catch(() => {});
    api<HistEvents>(`/v1/companies/${id}/history/events?types=asset_left_pipeline`).then(setEvents).catch(() => {});
  }, [id]);

  if (err) return <div className="notice err">{err}</div>;
  if (!summary || !phase || !ta) return <p className="muted">Loading history…</p>;

  const t = summary.totals;
  const quarantined = phase.quarters.filter((q) => q.quarantined).map((q) => q.period);
  const exits = events?.events ?? [];
  const discontinued = exits.filter((e) => e.exit_class === "likely_discontinued_early");
  const graduated = exits.filter((e) => e.exit_class !== "likely_discontinued_early");
  const sources = phase.quarters.filter((q) => q.source_url && !q.quarantined);

  return (
    <div>
      <p className="small"><Link href={`/companies/${id}`}>← {co?.name ?? "company"}</Link></p>
      <h1>Pipeline history{co?.ticker ? <span className="muted"> · {co.ticker}</span> : null}</h1>

      <div className="notice warn small" style={{ marginTop: 8 }}>
        Reconstructed from <b>{summary.pipeline_size_by_quarter.length} quarterly Wayback captures</b> (2021–2026).
        {quarantined.length > 0 && <> {quarantined.length} low-confidence quarter(s) ({quarantined.join(", ")}) are
        omitted from the charts (partial captures). </>}
        <b> Exits are classified, not assumed discontinued:</b> {summary.caveat}
      </div>

      <div className="kpis">
        <Stat n={t.assets_added} label="compounds entered" />
        <Stat n={t.phase_advances} label="phase advances" />
        <Stat n={t.discontinued_confirmed} label="discontinued (early-phase)" tone="red" />
        <Stat n={t.exits_approved_or_late} label="exited at Ph3/Filed (approval or late)" tone="blue" />
        <Stat n={t.partner_changes} label="partner changes" />
      </div>

      <div className="card">
        <div className="chart-head">
          <div>
            <div className="chart-title">Phase distribution over time</div>
            <div className="chart-sub muted small">by development program ({phase.unit})</div>
          </div>
          <button className="ghost small" onClick={() => setPct(!pct)}>{pct ? "Show counts" : "Show %"}</button>
        </div>
        <DistributionArea dist={phase} pct={pct} />
      </div>

      <div className="card">
        <div className="chart-head">
          <div>
            <div className="chart-title">Therapeutic-area mix over time</div>
            <div className="chart-sub muted small">by development program ({ta.unit}) · from the page&apos;s disclosed disease area</div>
          </div>
          <button className="ghost small" onClick={() => setPct(!pct)}>{pct ? "Show counts" : "Show %"}</button>
        </div>
        <DistributionArea dist={ta} pct={pct} />
      </div>

      <div className="card">
        <div className="chart-head">
          <div>
            <div className="chart-title">Pipeline flow: what entered and left</div>
            <div className="chart-sub muted small">by compound · entered (green) vs left — discontinued early (red) vs exited at Ph3/Filed (blue, ambiguous); line = pipeline size</div>
          </div>
        </div>
        <FlowChart summary={summary} />
      </div>

      <div className="grid2">
        <div className="card">
          <div className="chart-title">Confirmed discontinuations <span className="muted small">({discontinued.length})</span></div>
          <div className="chart-sub muted small">left from Phase 1/2 — most likely deprioritized</div>
          <ExitTable rows={discontinued} />
        </div>
        <div className="card">
          <div className="chart-title">Exited at Ph3/Filed <span className="muted small">({graduated.length})</span></div>
          <div className="chart-sub muted small">approval-and-graduated OR late failure — needs an external signal to tell apart, so <b>not</b> counted as discontinuations</div>
          <ExitTable rows={graduated} />
        </div>
      </div>

      {sources.length > 0 && (
        <p className="small muted" style={{ marginTop: 8 }}>
          Sources:{" "}
          {sources.map((q, i) => (
            <span key={q.period}>
              {i > 0 ? " · " : ""}
              <a href={q.source_url!} target="_blank" rel="noreferrer">{q.period}</a>
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

function Stat({ n, label, tone }: { n: number; label: string; tone?: "red" | "blue" }) {
  const color = tone === "red" ? "#a23b32" : tone === "blue" ? "#1f5fae" : "var(--ink)";
  return (
    <div className="stat">
      <div className="stat-n" style={{ color }}>{n}</div>
      <div className="stat-l muted small">{label}</div>
    </div>
  );
}

function ExitTable({ rows }: { rows: HistEvents["events"] }) {
  if (rows.length === 0) return <p className="muted small">none</p>;
  return (
    <table style={{ marginTop: 8 }}>
      <thead><tr><th>compound</th><th>last phase</th><th>~when</th></tr></thead>
      <tbody>
        {rows.slice(0, 60).map((e, i) => (
          <tr key={i}>
            <td>{e.asset ?? "—"}</td>
            <td className="small">{e.last_phase ?? "—"}</td>
            <td className="small muted">{e.eff_min ?? "?"} … {e.eff_max ?? "?"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
