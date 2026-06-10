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
  indication: string;
  indication_verbatim: string | null;
  phase_code: string | null;
  phase_label: string | null;
  phase_verbatim: string | null;
  status: string | null;
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
  statuses: string[];
};
