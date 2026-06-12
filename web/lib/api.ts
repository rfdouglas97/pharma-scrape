export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function api<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`);
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const screenshotUrl = (snapshotId: string) =>
  `${API}/v1/snapshots/${snapshotId}/screenshot`;

// --- shared types ----------------------------------------------------------
export type CompanyRow = {
  company_id: string;
  name: string;
  ticker: string | null;
  country: string | null;
  n_programs: number;
  n_discontinued: number;
  last_fetched: string | null;
};

export type ProgramRow = {
  program_id: string;
  asset_id: string;
  asset_name: string;
  modality_code: string | null;
  modality_verbatim: string | null;
  mechanism_verbatim: string | null;
  target_symbols: string | null;
  target_source: string | null;
  indication: string;
  indication_verbatim: string | null;
  phase_code: string | null;
  phase_label: string | null;
  phase_verbatim: string | null;
  status: string | null;
  efo_curie: string | null;
  efo_label: string | null;
  therapeutic_area: string | null;
  company_id: string;
  company_name: string;
  ticker: string | null;
  source_url: string | null;
  fetched_at: string | null;
  snapshot_id: string | null;
};

export type ProgramSearch = {
  total: number;
  limit: number;
  offset: number;
  results: ProgramRow[];
};

export type Facets = {
  phases: { code: string; label: string }[];
  modalities: string[];
  therapeutic_areas: string[];
  statuses: string[];
};

// --- history (longitudinal) ------------------------------------------------
export type HistQuarter = {
  period: string;
  captured_at: string;
  source_url: string | null;
  quarantined: boolean;
  total: number;
  counts: Record<string, number>;
};
export type HistDistribution = {
  company_id: string;
  dim: string;
  unit: string;
  buckets: string[];
  quarters: HistQuarter[];
};
export type HistEvent = {
  event_type: string;
  asset: string | null;
  asset_id: string | null;
  period: string | null;
  from_phase: string | null;
  to_phase: string | null;
  direction: string | null;
  last_phase: string | null;
  exit_class: string | null;
  partner: string | null;
  eff_min: string | null;
  eff_max: string | null;
  status: string;
};
export type HistEvents = { company_id: string; count: number; events: HistEvent[] };
export type HistSummary = {
  company_id: string;
  pipeline_size_by_quarter: {
    period: string;
    captured_at: string;
    pipeline_size: number;
    quarantined: boolean;
  }[];
  per_quarter: {
    period: string;
    added: number;
    discontinued: number;
    exit_approved_or_late: number;
    advances: number;
  }[];
  totals: {
    assets_added: number;
    phase_advances: number;
    discontinued_confirmed: number;
    exits_approved_or_late: number;
    partner_changes: number;
  };
  caveat: string;
};
