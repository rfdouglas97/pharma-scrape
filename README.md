# Pharma Pipeline Intelligence

An **autonomous factory** that builds and maintains a structured database of pharma/biotech
development pipelines — scraped from each company's own public disclosures, normalized into an
ID-anchored schema, quality-gated, and exposed for biology-aware search **and as a live read-only
SQL surface for downstream systems** (e.g. the Project Rand quant trading engine). Built to scale
from a **ticker list to hundreds of companies, hands-off.**

Give it a name + ticker and it resolves the company's pipeline page, scrapes it (whatever the
format), extracts the programs, QA-gates them, and loads them to gold — quarantining anything
it can't verify instead of publishing bad data.

> Historical design docs (the original engineering plan, the Codex factory plan, the Wayback
> backfill, the product brief) live in [`docs/`](docs/).

## The autonomous flow (per company)

```
name + ticker
  └─ resolve  : firecrawl search "<company> pipeline" → content-check the own-domain result
                (fallback: firecrawl map the site for "pipeline"). No URL guessing.
  └─ ingest   : Playwright render (+ Firecrawl scrape fallback, ignore-cert); if the page links
                a cleaner pipeline FILE (xlsx/pdf/csv), promote and ingest that instead.
  └─ classify : html_table | js_cards | image_page | pipeline_page | {csv,xlsx,pdf}_doc
  └─ extract  : route to the right extractor —
                  • document  → parse the file to a table, extract from text
                  • image     → two-pass vision (transcribe chart rows → normalize → vision QA)
                  • text-rich → text-only extraction (fast)
                  • else      → text + vision
  └─ QA       : LLM-as-judge + deterministic checks + trusted-count completeness gate
  └─ load     : gated upsert to gold (SCD2 program history). Failures → needs_repair, not gold.
  └─ enrich   : map indications → EFO/MONDO (OLS) + Open Targets, build adjacency closure for
                biology-aware search. Authority-validated, never overwrites scraped values.
  └─ publish  : refresh the `published` contract schema (born-on-dated nodes + mechanism/
                competitive edges) the trading system reads over a live read-only connection.
```

Every gold value traces back to an immutable bronze snapshot (HTML / screenshot / file).

## Quickstart

Prereqs: [`uv`](https://docs.astral.sh/uv/), Docker.

```bash
uv sync
uv run playwright install chromium
cp .env.example .env                 # set ANTHROPIC_API_KEY (and FIRECRAWL_API_KEY for discovery)
docker compose up -d                 # Postgres 16 + pgvector on :5433
uv run alembic upgrade head          # schema
uv run pipeline seed                 # controlled vocab + company registry (idempotent)
```

### Onboard a company autonomously (the headline)

```bash
uv run pipeline onboard --company "Insmed" --ticker INSM
#   resolve pipeline URL → register → render → extract → QA → gated load → loaded_gold
```

Feed a ticker list and the factory fills gold, quarantining the hard cases:

```bash
uv run pipeline batch --limit 20 --concurrency 4    # run the gated factory over the registry
uv run pipeline coverage                            # who loaded, who needs_repair, by source format
uv run pipeline repair --company "Insmed"           # reset a company sourced from the wrong page, re-onboard clean
```

### Walk the universe (market-cap order)

Onboard companies straight from the external company DB, ascending by market cap, resuming where
it left off — every attempt (including `unresolved`) is recorded so nothing is re-tried blindly:

```bash
uv run pipeline onboard-universe --limit 50         # systematic walk via the registry
uv run pipeline universe-status                     # resolved / unresolved / loaded progress
```

### Biology-aware enrichment

```bash
uv run pipeline enrich                               # indications → EFO/MONDO + Open Targets, adjacency closure
```

### Completeness gate (trusted counts)

`evals/expected_counts.yaml` holds human-curated "this company has N programs" checks. Promote
them into the QA gate so an incomplete scrape hard-fails instead of silently passing:

```bash
uv run pipeline load-eval-counts        # evals/ counts → known_expected_count on sources
```

### Extraction quality gate (golden set)

```bash
uv run pipeline eval                    # score live extraction vs labeled goldens; ≥0.95P / ≥0.90R
```

`pipeline eval` certifies extraction accuracy before scaling — golden fixtures can be
auto-reconciled from a company's own downloadable file (scored on the dimensions that file covers).

### Browse it

```bash
uv run uvicorn api.main:app --port 8000          # read + review API
cd web && npm install && npm run dev             # Next.js explorer at http://localhost:3000
```

The explorer is faceted, biology-aware program search; `/review` shows each extraction beside its
source screenshot for labeling golden fixtures.

### Share with a downstream system (the trading DB)

Expose the drug-level knowledge graph to an external consumer (the Project Rand quant engine) as a
**live, read-only SQL surface** — a `published` schema of spec-shaped views + born-on-dated
mechanism/competitive edges, behind a least-privilege role (`rand_reader`). Direct SQL, not the
REST API: a per-lookup HTTP round-trip is too slow for a backtest, and SCD2 history + `born_on`
let the *live* DB answer both "what's true now" and "what did we know as-of date X" (no look-ahead).

```bash
uv run pipeline publish --init    # (re)create the published schema, views, matviews, reader role
uv run pipeline publish           # refresh born_on + edges (run after each batch/enrich cycle)
```

Consumers attach over Postgres read-only (DuckDB `ATTACH` example, full schema, and a drop-in
point-in-time graph client in [`docs/PUBLISHED_DB.md`](docs/PUBLISHED_DB.md)).

## Layout

| Path | What |
|---|---|
| `pipeline_intel/onboard.py` | Capstone: name+ticker → resolve → register → scrape |
| `pipeline_intel/universe.py` | Systematic market-cap-ordered walk over the external company DB, resumable |
| `pipeline_intel/company_resolver.py` | Search-then-crawl pipeline-URL resolver (Firecrawl) + content validation |
| `pipeline_intel/source_discovery.py` | Find a cleaner pipeline FILE (xlsx/pdf/csv) linked off the page |
| `pipeline_intel/firecrawl_client.py` | Firecrawl search / map / scrape (REST, optional) |
| `pipeline_intel/batch.py` | Concurrent gated factory: render→extract→QA→load state machine |
| `pipeline_intel/model_routing.py` | Pick the extraction model per source format / size |
| `pipeline_intel/maintenance.py` | Reset a company's bad data (wrong source page) for a clean re-onboard |
| `pipeline_intel/ingest/` | Render (Playwright), document fetch+parse, source classification, snapshots, storage |
| `pipeline_intel/extract/` | Extraction schema, text/document extractor, two-pass visual extractor, batch API |
| `pipeline_intel/quality/` | LLM-as-judge QA, golden-set scorer + eval gate, fixture labeling |
| `pipeline_intel/evals.py` + `evals/` | Trusted-count completeness gate (curated ground truth) |
| `pipeline_intel/coverage.py` | Factory observability report |
| `pipeline_intel/gold/` | Medallion schema (SQLAlchemy) + thin silver→gold loader (SCD2) |
| `pipeline_intel/normalize/`, `ontology/`, `search/` | Vocab normalization, EFO/MONDO mapping + adjacency, query layer |
| `pipeline_intel/history/` | Longitudinal change-event feed (program_version diffs) |
| `pipeline_intel/publish/` | `published` contract schema (spec-shaped views + born-on/edge matviews + reader role) for the trading DB |
| `api/`, `web/` | FastAPI read+review service; Next.js explorer/review UI |
| `config/`, `migrations/` | Registry + vocab seeds; Alembic |

## Configuration (`.env`)

- `DATABASE_URL` — Postgres (local default matches `docker-compose.yml`).
- `ANTHROPIC_API_KEY` — extraction + QA (Claude). `FIRECRAWL_API_KEY` — discovery + JS-page fallback (optional; those paths no-op without it).
- `ARTIFACT_BACKEND` — `local` (dev) or `s3` (Supabase Storage / R2), same key scheme.
- Good-citizen scraping: robots respected, rate-limited; certs ignored for scrappy small-cap sites (public pages only).

## Tests / lint

```bash
uv run ruff check pipeline_intel/ api/ tests/
uv run pytest -q          # DB-backed tests skip if no Postgres is reachable
```
