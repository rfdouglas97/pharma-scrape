"use client";

import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HistDistribution, HistSummary } from "@/lib/api";

// Phase maturity gradient (preclinical -> approved); therapeutic-area palette.
const PHASE_COLORS: Record<string, string> = {
  Preclinical: "#cbd5e1", "Phase 1": "#93c5fd", "Phase 1/2": "#60a5fa", "Phase 2": "#3b82f6",
  "Phase 2/3": "#2563eb", "Phase 3": "#1d4ed8", Filed: "#7c3aed", Approved: "#16a34a",
  Discontinued: "#a23b32", unmapped: "#e5e7eb",
};
const TA_COLORS: Record<string, string> = {
  Oncology: "#2563eb", "Immunology & Inflammation": "#16a34a", Neuroscience: "#9333ea",
  Cardiovascular: "#dc2626", "Hematology (non-malignant)": "#0891b2", "Metabolic & Endocrine": "#d97706",
  Respiratory: "#0d9488", "Infectious Disease & Vaccines": "#db2777", "Other / Uncategorized": "#94a3b8",
};
const colorFor = (b: string, dim: string) =>
  (dim === "phase" ? PHASE_COLORS[b] : TA_COLORS[b]) || "#94a3b8";

// Build Recharts rows; quarantined quarters become null so the stacked area GAPS (never plotted).
function toRows(dist: HistDistribution, pct: boolean) {
  return dist.quarters.map((q) => {
    const row: Record<string, number | string | null | boolean> = {
      period: q.period,
      _quarantined: q.quarantined,
    };
    for (const b of dist.buckets) {
      if (q.quarantined) row[b] = null;
      else row[b] = pct ? (q.total ? +((100 * (q.counts[b] || 0)) / q.total).toFixed(1) : 0) : (q.counts[b] || 0);
    }
    return row;
  });
}

export function DistributionArea({ dist, pct }: { dist: HistDistribution; pct: boolean }) {
  const rows = toRows(dist, pct);
  return (
    <ResponsiveContainer width="100%" height={320}>
      <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
        <XAxis dataKey="period" tick={{ fontSize: 11 }} interval={1} />
        <YAxis tick={{ fontSize: 11 }} domain={pct ? [0, 100] : [0, "auto"]}
          ticks={pct ? [0, 25, 50, 75, 100] : undefined}
          tickFormatter={(v) => (pct ? `${v}%` : `${v}`)} width={48} />
        <Tooltip formatter={(v: number) => (pct ? `${v}%` : v)} contentStyle={{ fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {dist.buckets.map((b) => (
          <Area key={b} type="monotone" dataKey={b} stackId="1" stroke={colorFor(b, dist.dim)}
            fill={colorFor(b, dist.dim)} fillOpacity={0.82} connectNulls={false} isAnimationActive />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

// Diverging flow: additions up (green); exits down, split discontinued (red) vs approved/late (blue);
// pipeline-size line on the right axis. Discontinued != all exits — that separation is the point.
export function FlowChart({ summary }: { summary: HistSummary }) {
  const sizeByPeriod = new Map(summary.pipeline_size_by_quarter.map((s) => [s.period, s]));
  const rows = summary.pipeline_size_by_quarter.map((s) => {
    const q = summary.per_quarter.find((p) => p.period === s.period);
    return {
      period: s.period,
      added: q?.added ?? 0,
      discontinued: -(q?.discontinued ?? 0),
      exitOther: -(q?.exit_approved_or_late ?? 0),
      size: s.quarantined ? null : s.pipeline_size,
    };
  });
  void sizeByPeriod;
  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={rows} stackOffset="sign" margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
        <XAxis dataKey="period" tick={{ fontSize: 11 }} interval={1} />
        <YAxis yAxisId="flow" tick={{ fontSize: 11 }} width={32} />
        <YAxis yAxisId="size" orientation="right" tick={{ fontSize: 11 }} width={36} />
        <Tooltip contentStyle={{ fontSize: 12 }}
          formatter={(v: number, n: string) => [Math.abs(v), n === "size" ? "compounds" : n]} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <ReferenceLine yAxisId="flow" y={0} stroke="#999" />
        <Bar yAxisId="flow" dataKey="added" name="entered" stackId="f" fill="#16a34a" />
        <Bar yAxisId="flow" dataKey="discontinued" name="discontinued (early)" stackId="f" fill="#dc2626" />
        <Bar yAxisId="flow" dataKey="exitOther" name="exited — approval/late (ambiguous)" stackId="f" fill="#2563eb" />
        <Line yAxisId="size" type="monotone" dataKey="size" name="size" stroke="#111" strokeWidth={2}
          dot={false} connectNulls={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
