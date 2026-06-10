# Pharma Pipeline Intelligence

A continuously-maintained dataset of the development pipelines of the world's largest
pharma/biotech companies, scraped from their public pipeline disclosures, normalized
into an ID-anchored schema, and exposed for biology-aware search — built for commercial
biopharma investing.

See [`pharma-pipeline-intelligence-brief.md`](pharma-pipeline-intelligence-brief.md) for scope
and [`ENGINEERING_PLAN.md`](ENGINEERING_PLAN.md) for the full architecture and milestones.

## Status

**M0 (Foundations) — complete.** Repo + env, local Postgres+pgvector, full schema v1
(bronze/silver/gold with SCD2 program history), vocab + company-registry seeds, and an
end-to-end ingest stage (fetch → render → snapshot) with content-hash skip and provenance
artifacts.

**M1 (Extraction core) — built; pending live validation.** Canonical extraction schema,
versioned vision prompt, Claude Opus 4.8 structured-output extractor (`messages.parse` +
adaptive thinking, screenshot-authoritative), and the golden-set eval harness with a
field-level precision/recall scorer. The M1 **gate** (field precision ≥0.95, recall ≥0.90
on a format-diverse golden set) is enforced by `pipeline eval`. Running live extraction and
the eval needs `ANTHROPIC_API_KEY`. Next: label the golden set, clear the gate, then **M2
(normalize / resolve / enrich)**.

## Quickstart

Prereqs: [`uv`](https://docs.astral.sh/uv/), Docker.

```bash
uv sync                          # install Python deps (manages Python 3.12)
uv run playwright install chromium
cp .env.example .env             # local defaults match docker-compose
docker compose up -d             # Postgres 16 + pgvector on :5433

uv run alembic upgrade head      # apply schema
uv run pipeline seed             # load vocab + company registry (idempotent)
uv run pipeline companies        # list the registry

uv run pipeline run --company Moderna   # ingest one company (fetch→render→snapshot)
```

Run it twice: the second run records an `unchanged` snapshot via content-hash skip and
writes no new artifacts — the mechanism that keeps weekly re-scraping cheap.

### Extraction & the golden-set gate (M1)

Set `ANTHROPIC_API_KEY` in `.env`, then:

```bash
uv run pipeline run --company Moderna --extract   # ingest + extract changed snapshots
uv run pipeline extract --company Moderna         # extract latest snapshot only

# Build the golden set: scaffold a fixture from a real snapshot, label it, then evaluate
uv run pipeline golden-scaffold --company Moderna --format image  # copies text+screenshot
#   -> edit tests/golden/moderna/expected.json with the hand-labeled pipeline
uv run pipeline eval                              # scores extraction, applies the gate
```

`pipeline eval` exits non-zero unless field precision ≥0.95 and recall ≥0.90 across the
golden set — the rule that keeps us from scaling extraction past the pilot on bad data.
Cover format-diverse pages (static table, JS dashboard, PDF, image-only chart). Pass
`--model claude-sonnet-4-6` to compare a cheaper tier against the gate.

## Layout

| Path | What |
|---|---|
| `pipeline_intel/gold/models.py` | Full medallion schema (SQLAlchemy) |
| `pipeline_intel/ingest/` | Render (Playwright), hashing, storage, snapshot writer, per-company runner |
| `pipeline_intel/extract/` | Extraction schema, versioned prompt, Claude vision extractor |
| `pipeline_intel/quality/` | Golden-set eval harness, field-level scorer, fixture scaffolder |
| `pipeline_intel/registry/` | Vocab + company-registry seed loaders |
| `config/` | `companies.seed.yaml`, `vocab/phase.yaml`, `vocab/modality.yaml` |
| `migrations/` | Alembic |
| `tests/` | Unit + DB-backed tests |

## Tests / lint

```bash
uv run ruff check .
uv run pytest -q          # DB-backed tests skip if no Postgres is reachable
```

## Notes

- **Storage** is local filesystem in dev (`./artifacts`), swappable to S3-compatible
  (Supabase Storage / R2) by setting `ARTIFACT_BACKEND=s3` — same key scheme.
- **Registry URLs** in the seed are best-effort anchors for the top-cap pharma and are
  verified/corrected during M1 rendering (the DB is the source of truth at runtime).
- **Good-citizen scraping**: robots.txt is respected, requests are rate-limited and
  identify the crawler (see `.env.example`).
