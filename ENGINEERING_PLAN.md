# Pharma Pipeline Intelligence — Engineering Plan

**Status:** Approved scope, ready to build
**Inputs:** `pharma-pipeline-intelligence-brief.md` + decisions confirmed 2026-06-10:

| Decision | Choice |
|---|---|
| Hosting | Managed cloud (Supabase Postgres + storage, Fly.io worker/API, Vercel frontend) |
| Phase 1 UI | Functional explorer web app (search, filters, drill-down) — polish deferred to Phase 2 |
| Re-scrape cadence | **Weekly**, with content-hash skip so unchanged pages cost nothing to re-extract |
| Stack | Python ETL (Playwright + Claude API), PostgreSQL + pgvector single store, FastAPI service layer, Next.js explorer |

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

**M0 — Foundations (week 1)**
Repo scaffold, Supabase + Fly + Vercel provisioning, Alembic schema v1 (everything in §4), vocab seed files, company registry seeded with top 50 companies + pipeline URLs (research task — partly manual, partly Claude-assisted), CI.
*Done when:* `pipeline run --company pfizer` writes a snapshot row + artifacts to storage.

**M1 — Extraction core (weeks 2–3)**
Render/snapshot/hash for the pilot 10 (chosen for format diversity: static table, JS dashboard, accordion, PDF, image-only chart). Canonical Pydantic schema, extraction prompt, structured-output extractor, golden-set eval harness.
*Done when:* golden-set field precision ≥0.95 / recall ≥0.90 across all 10 formats. **This is the project's main risk gate — do not scale past it until it passes.**

**M2 — Normalize, resolve, enrich (weeks 3–4)**
Vocab normalization + review queue, entity resolution (exact/fuzzy/LLM-adjudicated), EFO load + closure build, OLS mapping pipeline, HGNC target normalization, Open Targets evaluation (go/no-go), SCD2 gold writer.
*Done when:* pilot 10 fully populated in gold; ≥85% of indications auto-mapped ≥0.8 confidence; partnered-asset dedupe demonstrated (e.g. an asset shared by two pilot companies resolves to one `asset_id`).

**M3 — Scale to 200 + weekly automation (weeks 5–6)**
Registry to ~200 companies, Batches API integration, GitHub Actions weekly workflow with matrix chunking, hash-skip, quality gates, coverage metrics, alerting.
*Done when:* a full unattended weekly run completes with ≥90% company success rate and a coverage report.

**M4 — Search + API (weeks 6–7)**
Facets + adjacency + vector search, hybrid ranking, Voyage embedding job, FastAPI endpoints, API-key auth, deploy.
*Done when:* "clinical-stage IL-23 programs in IBD *and adjacent GI indications*" returns correct, provenance-linked results via `curl`.

**M5 — Web explorer (weeks 7–9)**
Next.js app: search, asset/company pages, ops/review page, Supabase auth, deploy.
*Done when:* you can run a real research workflow end-to-end in the browser, and the review queue is triagable without SQL.

**Phase 2 backlog (designed-for, not built):** change-event generation from `program_version` diffs (phase moves, additions, discontinuations) + point-in-time queries; customer API keys/rate limits/billing; MCP server over `pipeline_intel.search`; agentic NL search; polished product UI; additional sources (ClinicalTrials.gov cross-referencing).

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
