# Pharma Pipeline Intelligence — Engineering Plan

**Status:** In build — M0–M2 + M4 enrichment complete (foundations → extraction → gold → indication/therapeutic-area/target normalization, with disclosed-first provenance). Eval gate (M3) sequenced before scale-out; entity resolution deferred to scale. A **downstream `published` surface** (live read-only SQL for the Project Rand trading engine) now sits on top of gold. See §0.
**Repo:** github.com/rfdouglas97/pharma-scrape (private)
**Inputs:** `pharma-pipeline-intelligence-brief.md` + decisions confirmed 2026-06-10:

| Decision | Choice |
|---|---|
| Hosting | Managed cloud (Supabase Postgres + storage, Fly.io worker/API, Vercel frontend) |
| Phase 1 UI | Functional explorer web app (search, filters, drill-down) — polish deferred to Phase 2 |
| Re-scrape cadence | **Weekly**, with content-hash skip so unchanged pages cost nothing to re-extract |
| Stack | Python ETL (Playwright + Claude API), PostgreSQL + pgvector single store, FastAPI service layer, Next.js explorer |
| Eval sequencing (2026-06-10) | **Golden-set labeling/eval happens through the review UI, not by hand-editing JSON.** UI brought forward ahead of the gate; ingestion does not scale to 200 until the gate passes. |

---

## 0. Current Status (2026-06-11)

| Milestone | State |
|---|---|
| **M0 Foundations** | ✅ **Complete.** Repo+env, Postgres+pgvector (docker-compose; Supabase in prod), full schema v1 (SCD2, ontology closure, pgvector), vocab + 20-company registry seeds, ingest stage (render→hash-skip→snapshot) with provenance artifacts, CLI, CI. |
| **M1 Extraction core** | ✅ **Built & live-validated.** Canonical schema, versioned vision prompt, Claude Opus 4.8 extractor (streaming + tiled screenshots), golden-set scorer + harness. Validated live on 4 format-diverse pages: Moderna (image-only), BMS (table — matches stated count), Lilly, GSK (matches their spreadsheet ~100% recall). |
| **M2 Gold loader + API + UI** | ✅ **Complete.** Thin silver→gold loader (SCD2, exact-synonym dedup, provenance; ~180 assets / 287 programs), phase/modality normalizer, shared query layer, FastAPI read+review service, Next.js explorer + review UI. Active vs discontinued segregated; phase shown normalized. |
| **M4 Enrichment** | ✅ **Indication + therapeutic-area + target/MoA enrichment done.** (1) Indication→MONDO mapping **~84% auto** (183/217, from 17%; recall fixed via exact-label-first search). (2) EFO/MONDO **adjacency** closure → biology-aware search (live: "lung carcinoma" → 40 NSCLC/SCLC subtype programs). (3) **Therapeutic-area classification** (Oncology, Immunology, Neuroscience, Infectious & Vaccines, Cardiometabolic, …) via MONDO ancestry — filter + column in the UI. (4) **Mechanism/MoA is a first-class *disclosed* field** (column in the explorer; searchable). (5) **Open Targets backfill** of the molecular target — **only for assets that disclosed no MoA/target** (39 targets on 21 gap assets), provenance-tagged. ⬜ Pending (scale-time): fuzzy/LLM entity resolution, target-string normalization. |
| **M3 Eval gate** | ⏳ **Sequenced before scale, not now.** Strong *informal* ground-truth validation (GSK/BMS match their own published numbers); golden labels are durable page-truth, so formal labeling is deferred until just before scale-out. Threshold: precision ≥0.95 / recall ≥0.90. Harness refuses to score unlabeled drafts. |
| **M6 Downstream `published` surface** | ✅ **Built & live-verified.** A stable, spec-shaped (`robinhood/design/pipeline_db_spec.md`) schema of views + born-on-dated mechanism/competitive edge matviews, served over a live read-only Postgres role (`rand_reader`) — the channel the Project Rand quant engine reads (direct SQL, not the REST API; SCD2 + `born_on` make it point-in-time for backtests, no look-ahead). Built as idempotent DDL via `pipeline publish` (not Alembic — the layer is a regenerable contract). Verified: 1,280 programs / 16,734 edges, reader sandboxed to `published.*`, as-of filtering excludes later-born edges, PDCD1 resolves to mechanism peers across 3 companies. See [`PUBLISHED_DB.md`](PUBLISHED_DB.md). ⬜ Pending: `cik`/`is_lead` population; optional ClinicalTrials.gov join for trial-registration-anchored `born_on`. |

**What this changes:** the original plan ran enrichment (M2) → scale (M3) → API (M4) → UI (M5). We now bring a **thin gold loader + API + explorer/review UI forward** (new M2) so the eval can run through it (new M3), *then* do full enrichment (M4) and scale (M5). The risk discipline is intact: **we do not scale ingestion past the pilot, or treat the data as sellable, until the gate passes.** Building the UI on an unvalidated extractor is low-risk — the loader rebuilds from immutable silver, and the UI is format-agnostic.

**Decisions / findings (2026-06-11):**
- **Provenance principle (load-bearing): disclosed always wins; enrichment is a labeled supplement on genuine gaps only.** Every field carries a source (`disclosed` vs `open_targets`). External enrichment may *only* fill what the company left blank and is always tagged — it never overwrites or takes credit for company-disclosed data. *Caught and fixed:* the loader had been burying companies' disclosed Mode-of-Action in a catch-all `extras` blob, so it looked "not disclosed" and Open Targets then backfilled it and stamped it `open_targets` — i.e. crediting an external DB for what the company published (37 GSK assets). MoA is now a first-class disclosed field; Open Targets only fills the target gene for assets that disclosed nothing; modality is **never derived** — only set when the company states it. This is the kind of integrity error the M3 eval gate exists to catch.
- **Why the gate is not run "now":** its sole purpose is to certify extraction before scaling to ~200 and before selling — a gate-before-scale, not an immediate task. With GSK/BMS already matching their own published numbers, and golden labels being model-independent page-truth (re-scored for free after any prompt change), the efficient order is *refine extractor → label once → run gate just before scale*. Non-scaling work proceeds in parallel.
- **Frontend confirmed: Next.js** (App Router) on Vercel for the explorer/review UI — it's the eventual product surface, so we build on it from M2 rather than a throwaway.
- **GSK extraction validated against authoritative ground truth** (GSK's own downloadable Q1-2026 pipeline spreadsheet, in `Eval_data/`, gitignored). Result: we captured **all 57 unique compounds** (recall ~100%, incl. linerixibat); the only true gap was one brand-name synonym ("Lynavoy"). Our 5 "extra" assets were real page content from the page's **"Pipeline changes / Removed"** section, correctly tagged `Removed`. The headline "76" the page implies = the spreadsheet's 76 **program rows** (compound × indication), not 76 assets.
- **To-do — prompt refinement (before M3 gate):** segregate "Pipeline changes / Added / Removed" sections from the active pipeline (e.g. status=`removed`/`discontinued` and exclude from active counts) so active-program counts reconcile cleanly with company-stated totals.
- **To-do — source strategy (M5):** where a company publishes a **downloadable pipeline file** (xlsx/PDF, as GSK does), prefer ingesting that structured file over scraping the rendered page — cleaner, more complete, and lower extraction risk. Add `source_type=pipeline_file` handling to the registry/ingest path. Such files also make the best golden-set ground truth.

---

## 1. Architecture Overview

```
                        ┌─────────────────────────────────────────────┐
                        │                INGESTION (Python)            │
 company registry ──▶   │  fetch ▶ render (Playwright) ▶ snapshot      │
 (DB table, versioned)  │  ▶ hash-check ▶ LLM extract (Claude, vision) │
                        │  ▶ validate ▶ normalize ▶ entity-resolve     │
                        │  ▶ ontology-map ▶ upsert gold (SCD2)         │
                        └──────────────┬──────────────────────────────┘
                                       │
        ┌───────────────┐   ┌──────────▼──────────┐   ┌──────────────────┐
        │ Object storage │   │  PostgreSQL (one DB) │   │  Review queue     │
        │ raw HTML / PNG │◀──│  bronze: snapshots   │──▶│  unmapped vocab,  │
        │ PDF artifacts  │   │  silver: extractions │   │  low-conf mappings│
        └───────────────┘   │  gold: entities+SCD2 │   └──────────────────┘
                            │  + pgvector + closure │
                            └──────────┬───────────┘
                                       │
                            ┌──────────▼───────────┐
                            │  Query/service layer  │  (one Python package:
                            │  facets + ontology    │   shared by all surfaces)
                            │  traversal + vector   │
                            └───┬───────┬───────┬──┘
                                │       │       │
                          FastAPI    Next.js   (Phase 2: MCP server,
                          REST API   explorer   public API product)
```

**Medallion layers, all in one Postgres + one object-storage bucket:**

- **Bronze** — immutable: every fetch stores the rendered HTML, full-page screenshot(s), and any PDFs in object storage, plus a `snapshot` row (URL, timestamp, content hash, artifact keys). Never updated, never deleted. This is the provenance backbone and the Phase 2 change-tracking foundation.
- **Silver** — the validated LLM extraction JSON per snapshot (`extraction` rows). Re-extractable from bronze at any time (model upgrades, prompt fixes) without re-scraping.
- **Gold** — normalized, ID-anchored entities (`company`, `asset`, `program`, …) with SCD2 effective dating on program state.

### Key design decisions (resolving the brief's open questions)

| Open question | Decision | Rationale |
|---|---|---|
| EFO vs MONDO | **EFO primary, MONDO stored as crosswalk** | Open Targets keys on EFO; OLS serves both; EFO's `is-a` hierarchy drives adjacency. MONDO IDs stored alongside for future interop. |
| Open Targets vs compose OLS+ChEMBL+UniProt | **OLS for indication mapping; Open Targets GraphQL for target/drug enrichment** | OLS is the right tool for label→ID resolution; Open Targets is the highest-leverage single source for target↔disease↔drug links once IDs exist. Evaluate in M2 with a hard go/no-go. |
| Single-store vs polyglot | **Single Postgres** (pgvector for semantic search, transitive-closure table for ontology traversal) | At this scale (~tens of thousands of programs, ~100k ontology terms) Postgres handles all three search modes. A graph DB or dedicated vector store is unjustified complexity; revisit only if closure queries exceed ~100ms. |
| Bitemporal vs SCD2 | **SCD2 (valid_from/valid_to) on `program_version` + immutable snapshots as transaction time** | Full bitemporal tables are overkill; snapshots already give "what did we know when," SCD2 gives "what was true when." Together they answer every Phase 2 history question. |
| Build vs buy rendering | **Build: Playwright in our worker** | Per-site config is light; paid rendering APIs add cost and lose control over artifact capture. |

---

## 2. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language / env | Python 3.12, `uv`, `ruff`, `pytest` | Single package `pipeline_intel/` |
| Rendering | Playwright (Chromium, headless) | Full-page screenshots + DOM HTML + network-idle wait; per-company overrides in registry config |
| Extraction LLM | **Claude Opus 4.8** (`claude-opus-4-8`) via `anthropic` SDK | Vision for image/PDF pipeline charts; structured outputs (`client.messages.parse` + Pydantic); **Batches API for the weekly run (50% cost)**; prompt caching on the shared system prompt + schema. Cheaper models (Sonnet 4.6 at $3/$15, Haiku 4.5 at $1/$5 per MTok) are an explicit cost lever you can choose after M1 quality evals — default stays Opus 4.8. |
| Embeddings | Voyage AI (`voyage-3` family) | Anthropic doesn't ship embeddings; Voyage is the recommended pairing. Stored in pgvector. |
| Database | PostgreSQL 16 on **Supabase** (+ `pgvector`) | Supabase also provides object storage and auth for the web app — one vendor for the data plane |
| Object storage | Supabase Storage (S3-compatible) | Bucket `raw-artifacts/`, keyed by `{company}/{date}/{hash}` |
| Migrations / ORM | SQLAlchemy 2.0 + Alembic | Typed models, versioned schema from day one |
| Orchestration | Plain Python CLI (`pipeline run …`) + **GitHub Actions weekly cron**, job state in Postgres | No Airflow/Dagster yet — a `job_run` table + idempotent per-company jobs is sufficient at weekly cadence. Upgrade path: Prefect, only if DAG complexity demands it. |
| Service layer / API | FastAPI on Fly.io (or Railway) | Read-only REST in Phase 1; the same internal query module backs UI, API, and future MCP |
| Web app | Next.js (App Router) on Vercel, Supabase Auth (magic link) | Utilitarian explorer; talks only to FastAPI |
| Observability | `job_run` table + coverage views + email/Slack webhook alert on failures | Sentry optional later |

---

## 3. Repository Layout

```
pharma_scrape/
├── pipeline_intel/                 # Python package (ETL + service layer)
│   ├── registry/        # company registry loaders + validation
│   ├── ingest/          # fetch.py, render.py, snapshot.py (bronze)
│   ├── extract/         # prompts/, schemas.py (Pydantic), extractor.py, batch.py (silver)
│   ├── normalize/       # vocab.py (phase/modality), resolver.py (entity resolution)
│   ├── ontology/        # ols_client.py, open_targets.py, closure.py, mapper.py
│   ├── gold/            # upsert.py (SCD2 writer), models.py (SQLAlchemy)
│   ├── search/          # facets.py, adjacency.py, vector.py, hybrid.py  ← the shared query layer
│   ├── quality/         # gates.py, review_queue.py, coverage.py
│   └── cli.py           # `pipeline run --company X`, `pipeline backfill`, `pipeline enrich`, …
├── api/                 # FastAPI app (thin wrapper over pipeline_intel.search)
├── web/                 # Next.js explorer
├── migrations/          # Alembic
├── config/
│   ├── companies.seed.yaml      # initial registry seed (loaded into DB; DB is source of truth after)
│   └── vocab/                   # phase.yaml, modality.yaml (versioned controlled vocabularies)
├── tests/               # unit + golden-set extraction evals
└── .github/workflows/   # weekly-scrape.yaml (chunked matrix), ci.yaml
```

---

## 4. Data Model (gold schema, DDL-level)

All IDs are app-generated ULIDs. Every gold row traces back to a `snapshot_id` + `extraction_id`.

### Identity & provenance

```sql
company            (company_id PK, name, ticker, exchange, country, website,
                    parent_company_id FK NULL, status, market_cap_usd, created_at)
company_source     (source_id PK, company_id FK, url, source_type,        -- pipeline_page | pdf_doc
                    render_config JSONB,                                   -- waits, clicks-to-expand, pagination
                    active BOOL, added_at)                                 -- registry = managed config, in DB
snapshot           (snapshot_id PK, source_id FK, fetched_at, http_status,
                    content_hash CHAR(64),                                 -- sha256 of normalized rendered DOM
                    html_key, screenshot_keys JSONB, pdf_keys JSONB,       -- object-storage keys
                    render_meta JSONB)
extraction         (extraction_id PK, snapshot_id FK, model, prompt_version,
                    extracted_at, raw_json JSONB,                          -- full LLM output, verbatim
                    status,                                                -- ok | failed | needs_review
                    usage JSONB, error TEXT)
```

### Entities

```sql
asset              (asset_id PK, preferred_name, modality_code FK NULL, modality_verbatim,
                    originator_company_id FK NULL, chembl_id NULL, description, created_at)
asset_synonym      (asset_id FK, synonym, synonym_type,                    -- dev_code|brand|generic|other
                    source_extraction_id FK, UNIQUE(asset_id, lower(synonym)))

indication         (indication_id PK, preferred_label)
indication_mapping (indication_id FK, ontology,                            -- EFO | MONDO
                    curie, label, confidence NUMERIC, method,              -- exact|ols_search|llm_assisted
                    status)                                                -- auto | reviewed | rejected | unmapped

target             (target_id PK, hgnc_symbol, uniprot_id NULL, ensembl_id NULL, name)
asset_target       (asset_id FK, target_id FK, verbatim, action NULL,      -- inhibitor/agonist/…
                    source_extraction_id FK)

-- program = the atomic unit: asset × indication × sponsoring company
program            (program_id PK, asset_id FK, indication_id FK, company_id FK,
                    UNIQUE(asset_id, indication_id, company_id))

-- SCD2 state history: one open row per program; closed rows are history
program_version    (version_id PK, program_id FK,
                    phase_code FK, phase_verbatim,
                    status,                       -- active | discontinued | paused | unknown
                    indication_verbatim,
                    extras JSONB,                 -- ALL company-specific fields (RoA, MoA text,
                                                  -- milestones, designations, territory…) — never dropped
                    valid_from TIMESTAMPTZ, valid_to TIMESTAMPTZ NULL,     -- NULL = current
                    first_seen_snapshot_id FK, last_seen_snapshot_id FK)
CREATE UNIQUE INDEX ON program_version(program_id) WHERE valid_to IS NULL;

partnership        (partnership_id PK, program_id FK NULL, asset_id FK NULL,
                    partner_company_id FK NULL, partner_name_verbatim,
                    role NULL, deal_type NULL, territory NULL, source_extraction_id FK)
```

### Vocabularies, ontology, search

```sql
phase_vocab        (code PK, label, sort_order, version)   -- preclinical … approved, discontinued
modality_vocab     (code PK, label, version)               -- small_molecule, mab, adc, bispecific, …
vocab_mapping      (vocab, verbatim, code FK NULL, confidence, status)     -- unmapped → review queue

ontology_term      (curie PK, ontology, label, synonyms JSONB, obsolete BOOL)
ontology_edge      (parent_curie FK, child_curie FK)                       -- direct is-a edges from EFO
ontology_closure   (ancestor_curie, descendant_curie, depth,               -- precomputed transitive closure
                    PRIMARY KEY(ancestor_curie, descendant_curie))

program_embedding  (program_id PK FK, embedding vector(1024), text_hash, model)

review_queue       (item_id PK, kind,            -- vocab_unmapped | ontology_lowconf | extraction_anomaly | dedupe_candidate
                    entity_ref JSONB, payload JSONB, status, created_at, resolved_at, resolution JSONB)
job_run            (run_id PK, kind, company_id NULL, started_at, finished_at,
                    status, stats JSONB, error TEXT)
```

**Rules baked into the schema:**
- Verbatim value stored next to every normalized value (`*_verbatim` columns + `extras` JSONB). Source text is never destroyed.
- Phase lives on `program_version`, never on `asset`.
- `extras` JSONB absorbs any company-specific field without schema churn; recurring fields get promoted to real columns later.
- The SCD2 partial unique index guarantees exactly one current state per program — Phase 2 change events are computed by diffing consecutive `program_version` rows, no migration needed.

---

## 5. Ingestion Pipeline (per company, idempotent)

```
1. LOAD      registry row(s): URLs + render_config
2. FETCH     robots.txt check → Playwright render (network-idle, expand accordions per config)
             → save DOM HTML + full-page screenshot(s) + linked pipeline PDFs → bronze
3. HASH      sha256 over normalized DOM text. If == previous snapshot's hash:
             record snapshot, mark "unchanged", touch last_seen on current program_versions, STOP.
             (This is what makes weekly cadence affordable.)
4. EXTRACT   Claude Opus 4.8, structured output against the canonical asset-row schema:
             input = cleaned page text + screenshot images (+ PDF pages when present)
             output = list[ExtractedProgram]: asset name, synonyms/codes, phase (verbatim),
                      indication (verbatim), target (verbatim), modality (verbatim),
                      partners, plus open `additional_fields` dict.
             Weekly bulk runs go through the Batches API (50% price, <24h turnaround — fine for weekly).
             Shared system prompt + schema carry cache_control for prompt-cache reuse.
5. VALIDATE  Pydantic schema; quality gates:
               - asset count vs previous snapshot (>40% drop ⇒ flag, don't publish)
               - required fields present per row
               - phase verbatim non-empty
             Failures → extraction.status = needs_review + review_queue item. Never silently publish.
6. NORMALIZE phase + modality via vocab_mapping (deterministic dictionary first,
             Claude fallback for novel strings, confidence-scored; unmapped → review queue)
7. RESOLVE   entity resolution to asset_id:
               a. exact match on name/synonym/dev-code (normalized casing/punctuation)
               b. fuzzy candidates (pg_trgm) → Claude adjudication with both assets' context
               c. ambiguous → dedupe_candidate review item; create provisional asset (merge later, keep alias)
             Partnered assets resolve to ONE asset_id; each company's view preserved via program rows.
8. UPSERT    gold SCD2: for each extracted program —
               unchanged state  → touch last_seen_snapshot_id
               changed state    → close current version (valid_to = now), open new version
               new program      → create program + first version
               missing from page→ DON'T auto-discontinue; after 2 consecutive missing scrapes,
                                  flag for review (pages get partially rendered; never silently delete)
9. ENRICH    (async, decoupled) ontology mapping + Open Targets + embeddings (see §6)
10. REPORT   job_run stats: rows extracted, mapped %, new/changed/flagged counts
```

**Scraping etiquette:** identified User-Agent with contact email, 1 req/sec/domain max, robots.txt respected, artifacts cached so a re-run never re-fetches within 24h.

**Failure isolation:** every company is an independent job; one broken site never blocks the run. GitHub Actions runs a matrix of ~10 chunks × ~20 companies.

---

## 6. Ontology Enrichment

**Indications → EFO (+ MONDO crosswalk):**
1. Local lookup against cached `ontology_term` (labels + synonyms) — exact/normalized match.
2. Miss → OLS v4 `/search` API (EFO first, MONDO fallback) → top candidates.
3. Ambiguous → Claude picks among OLS candidates given program context (asset, target, modality) → confidence score.
4. Every mapping stored with `method`, `confidence`, `status`; below threshold (e.g. 0.8) ⇒ `ontology_lowconf` review item. **Low-confidence mappings are flagged, never trusted blindly.**

**EFO hierarchy:** monthly job downloads the EFO OBO/OWL release, loads `ontology_term` + `ontology_edge`, recomputes `ontology_closure` (recursive CTE → materialized table). ~1–2M closure rows; trivial for Postgres.

**Targets:** verbatim target strings → HGNC approved symbols (HGNC REST + alias table), then UniProt/Ensembl IDs. Same confidence/review pattern.

**Open Targets (M2 evaluation):** GraphQL API keyed by (Ensembl ID, EFO ID) → known drugs, mechanism of action, ChEMBL IDs, target–disease association scores. Adopt if it enriches ≥60% of resolved programs with useful MoA/drug links; otherwise defer to Phase 2.

---

## 7. Search (the shared query layer — `pipeline_intel.search`)

Three primitives, fused by one hybrid entry point. All surfaces (API, web, future MCP) call this module only.

1. **Faceted filtering** — SQL over gold: phase, modality, company, target, status, ontology subtree. Fast, deterministic, the backbone of every query.
2. **Indication adjacency** — given an EFO term, `ontology_closure` expands to descendants (always), ancestors ≤ N hops, and siblings (shared parent), each tagged with `relation` + `distance`. "NSCLC" → all lung-carcinoma programs → thoracic-neoplasm programs, ranked by distance.
3. **Semantic vector search** — each program gets a composed description ("<asset>, a <modality> targeting <target>, in <phase> for <indication> by <company>; <MoA text>") embedded via Voyage into pgvector; cosine top-k catches similarity the hierarchy misses (mechanism-level adjacency, off-label phrasing).

**Hybrid ranking:** filters are hard constraints; ontology distance and vector similarity combine via weighted reciprocal-rank fusion. Exact indication > child > parent/sibling > vector-only, tunable weights in config.

**Phase 2:** an agentic layer (Claude + tool use, with these three primitives exposed as tools) decomposes natural-language investor queries. Phase 1 just keeps the primitives clean and composable.

---

## 8. Delivery Surfaces (Phase 1 scope)

**FastAPI** (read-only, versioned `/v1`):
- `GET /v1/search` — q + facet params + `adjacency=none|children|full`
- `GET /v1/assets/{id}`, `GET /v1/companies/{id}` (with full pipeline), `GET /v1/programs/{id}`
- `GET /v1/indications/{curie}/programs` — subtree-expanded
- `GET /v1/meta/coverage` — per-company freshness & quality stats
- Auth: single API key (you) in Phase 1; per-customer keys + rate limiting in Phase 2.
- Every response row carries provenance: `source_url`, `fetched_at`, `snapshot_id`.

**Next.js explorer:**
- Search page: query box + facet sidebar (phase, modality, company, target, indication-with-adjacency toggle) → results table
- Asset page: identity, synonyms, targets, all programs across companies, partnerships, provenance links (rendered screenshot viewable — investors can see the source)
- Company page: full pipeline grouped by phase, coverage freshness
- Ops page (you only): review queue triage, coverage dashboard, last-run status
- Supabase Auth magic-link; allowlist of emails.

---

## 9. Operations & Quality

- **Weekly run:** GitHub Actions cron (Sun 06:00 UTC) → matrix scrape jobs → batch extraction submit → poll → normalize/resolve/upsert → enrich → coverage report. Each stage idempotent; rerun-safe.
- **Coverage metrics (per company):** last successful snapshot age, extraction status, asset count trend, % phases mapped, % indications mapped, open review items. Surfaced in `/v1/meta/coverage` + ops page.
- **Alerting:** run summary (and any company in `failed`/`needs_review`) posted via email/Slack webhook at end of each weekly run.
- **Golden-set evals:** ~15 hand-labeled company pages (spanning HTML table / JS app / PDF / image chart) checked into `tests/golden/`; extraction changes (prompt or model) must hold precision/recall ≥ 0.95/0.90 on field-level comparison before deploy. This is the regression harness that makes the LLM extractor maintainable.
- **Cost guardrails:** per-run token budget logged from `usage`; alert if a weekly run exceeds 2× trailing average.

---

## 10. Cost Estimate (steady state, ~200 companies)

| Item | Estimate |
|---|---|
| Supabase (Pro) | $25/mo |
| Fly.io (API + occasional worker) | $10–20/mo |
| Vercel | $0–20/mo |
| GitHub Actions | ~free at weekly cadence |
| **LLM extraction** — Opus 4.8 via Batches (~40k in / 8k out per changed company ≈ $0.20 batched); weekly change rate ~20–40% → 40–80 companies/wk | **~$10–20/wk ≈ $40–80/mo** |
| One-time full backfill (200 companies, batched) | ~$40–60 |
| Normalization/resolution/ontology LLM calls + Voyage embeddings | <$10/mo |
| **Total** | **≈ $100–150/mo** |

Levers if cost matters later: drop extraction to Sonnet 4.6 (−40%) after golden-set evals prove parity, or move to biweekly for the long tail of slow-moving companies.

---

## 11. Milestones

*Re-sequenced 2026-06-10 (see §0): UI moved ahead of the eval gate. ✅ = done, 🟡 = built/pending, ⬜ = upcoming.*

**M0 — Foundations** ✅
Repo scaffold, Postgres+pgvector (docker-compose locally; Supabase in prod), Alembic schema v1 (everything in §4), vocab seeds, 20-company registry seed, ingest stage (render→hash-skip→snapshot) + provenance artifacts, CLI, CI.
*Done:* `pipeline run --company X` writes snapshot rows + artifacts; verified on Lilly/Moderna/BMS; hash-skip + robots compliance proven.

**M1 — Extraction core** 🟡 *(built & live-validated; gate deferred to M3)*
Canonical Pydantic schema, versioned vision prompt, Claude Opus 4.8 extractor (streaming, tiled screenshots, `needs_review` flagging), golden-set scorer + harness, fixture scaffolder. Live-validated on 4 format-diverse pages (image-only + 3 tables). Candidate golden set seeded as correctable drafts.
*Remaining:* the gate run itself — now happens in M3 through the review UI.

**M2 — Thin gold loader + API + explorer/review UI** ⬜ ← **NEXT**
- **Silver→gold loader (thin):** upsert extractions into `company/asset/program/program_version` using the *existing* phase/modality dictionary normalization; basic asset upsert (exact name/synonym match only — full entity resolution deferred to M4). Verbatim + `extras` preserved. Populates gold enough to browse. Rebuildable from immutable silver.
- **FastAPI read layer** over `pipeline_intel.search` (facets + drill-down; adjacency/vector deferred to M6).
- **Next.js explorer:** company/asset/program browse with provenance (source URL, fetch date, viewable screenshot) **+ a review surface**: extraction shown side-by-side with its screenshot, editable, save → writes the corrected `expected.json` and flips `meta.labeled=true` (this is the labeling tool that unblocks the gate).
*Done when:* you can browse the pilot pipelines in the browser and correct an extraction into a labeled golden fixture without touching JSON.

**M3 — Extractor refinement + golden gate** ⬜ — **the risk gate, run *before scale*, not now**
The gate's only job is to certify extraction accuracy **before** (a) scaling ingestion past the pilot and (b) selling the data — it is **not** a chore that must be cleared this moment. We already have strong *informal* validation: GSK matched its own downloadable pipeline spreadsheet at ~100% compound recall; BMS matched its self-stated "50 compounds"; Moderna's image-only extraction eyeballed clean. So this milestone is sequenced deliberately:
1. **Extractor refinements first** (cheaper to label against a clean draft): segregate "Pipeline changes / Added / Removed" sections from active pipeline; any other systematic fixes found.
2. **(Optional) broaden the pilot** for format diversity — a JS dashboard, a PDF.
3. **Label once**, against the refined extractor, via the review UI. For GSK, auto-reconcile against the uploaded spreadsheet. Golden labels are page-truth (verbatim), so they are **durable** — independent of the model; prompt changes only alter model output, which is re-scored against the same labels in seconds. The gate thus becomes a permanent regression harness.
4. **Run `pipeline eval`** and tune prompt + per-site `render_config` until **field precision ≥0.95 / recall ≥0.90** across formats.
*Done when:* gate passes. **Do not scale ingestion past the pilot, or treat the data as sellable, until here.** Non-scaling work (M4 enrichment, change-tracking design) can proceed in parallel and does not wait on the gate.

**M4 — Normalize, resolve, enrich** 🟢 *(indication + therapeutic-area + target/MoA done; ER deferred to scale)*
✅ **Done:** OLS indication→MONDO mapping (LLM normalize → exact-label-first search → LLM adjudicate; ~84% auto, broad-baskets → review queue), EFO/MONDO is-a closure (bounded, depth-capped, resumable) powering indication-adjacency search, therapeutic-area classification via MONDO ancestry (API + UI), MONDO-consistent dataset. **Mechanism/MoA promoted to a first-class *disclosed* field** (explorer column + searchable). **Open Targets backfill** of the molecular target — gaps-only, provenance-tagged, no modality derivation (see §0 provenance principle).
⬜ **Deferred to scale-time (no work at 4 pilot companies — no dup collisions, only 2 disclosed gene targets, so no test cases yet):** fuzzy/LLM-adjudicated entity resolution for partnered assets named differently; target-string normalization (verbatim → HGNC/UniProt aliases).
*Met:* ~84% of pilot indications auto-mapped (target was 85%); exact-synonym partnered-asset dedupe works (fuzzy is the scale-time upgrade).
⚠️ **Known limitation:** enrichment (OLS + Open Targets + LLM) is **sequential/slow** — fine for incremental, resumable re-runs, but **must be parallelized before the 200-company scale-out**.

**M5 — Scale to 200 + weekly automation** ⬜
Registry to ~200 (verify/correct seed URLs, add `render_config` per site), Batches API, GitHub Actions weekly cron with matrix chunking, quality gates, coverage metrics, alerting.
*Done when:* a full unattended weekly run completes with ≥90% company success rate + coverage report.

**M6 — Biology-aware search + API hardening** ⬜
Ontology-adjacency traversal + Voyage embeddings + hybrid ranking fused in `pipeline_intel.search`; per-customer API keys + rate limiting.
*Done when:* "clinical-stage IL-23 programs in IBD *and adjacent GI indications*" returns correct, provenance-linked results in the UI and via the API.

**Phase 2 backlog (designed-for, not built):** change-event generation from `program_version` diffs + point-in-time queries; customer billing; MCP server over `pipeline_intel.search`; agentic NL search; polished product UI; additional sources (ClinicalTrials.gov cross-referencing).

---

## 12. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Extraction quality on image-only / exotic pipeline pages | **High** — the core bet | M1 gate with format-diverse golden set; vision input is first-class; per-site `render_config` escape hatch; `needs_review` instead of silent bad data |
| Entity resolution errors (wrong merge worse than missed merge) | Medium | Conservative auto-merge (exact/dev-code only); fuzzy → review queue; merges reversible via alias table |
| Site blocks / ToS (commercial product) | Medium | Good-citizen scraping from day one; **get a legal read on ToS of the major targets before selling access** (flagged, not a build blocker) |
| Phase semantics differ per company ("Phase 2/3", "Registration", regional phases) | Medium | Verbatim always preserved; vocab is versioned + extensible; unmapped state instead of forced guesses |
| Weekly LLM cost creep as registry grows to 500 | Low | Hash-skip, Batches, model-tier lever, per-run budget alert |
| One-person ops burden | Medium | Review queue keeps human work batched + bounded; alerts only on real failures; everything idempotent/rerunnable |
| Building UI/loader before the extraction gate passes (re-sequence) | Low | Loader rebuilds from immutable silver; UI is format-agnostic; the only hard gate is *scaling ingestion / selling data*, which still waits for M3. Worst case: prompt tuning in M3 changes extraction output → re-run loader (cheap, idempotent). |
