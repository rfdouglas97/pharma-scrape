# Plan v2: Historical Pipeline Backfill via the Wayback Machine

> v2 supersedes v1 after a design review. Key changes: the **change-event feed is the product**
> (not a byproduct of SCD2); rendering uses **standard Wayback replay, not `id_`** (v1's choice was
> proven to load live-domain assets — see Gate 0); a **multi-page assembly layer**, **corporate-
> transaction handling**, a **durable decision store**, and **interval-dated changes** are added;
> **URL-drift discovery** is promoted to the critical path. Horizon fixed at **~5 years**.

## Status (2026-06-12)

**Validated end-to-end on Bristol Myers Squibb (5 years, 22 quarterly captures); core engine shipped
in PR #1 (`wayback-backfill`).**

| Piece | State |
|---|---|
| **Gate 0** (render fidelity) | ✅ Passed — standard replay, egress blocked, **22/22 rendered, zero live-domain leaks**, period-correct |
| **Extraction** | ✅ 22 quarters via Opus vision (older captures render phase as a chart → vision mandatory) |
| **Change-detection core** (`pipeline_intel/history/detect.py`) | ✅ Built + 9 unit tests; reproduces the BMS POC exactly |
| **Schema** (`captured_at`/`origin`/`extraction_quality_score`, `change_event`, `asset_alias`) | ✅ Migration applied + round-trips |
| **DB rebuild adapter + CLI** (`rebuild-history`) | ✅ Built + 2 DB integration tests |
| **Identity: deterministic + curated aliases** | ✅ Used in POC (269 names → 150 assets) |
| **Identity: LLM clustering** (`merge_assets.py`) | ⏳ Written, **blocked by API usage limit until 2026-07-01** |
| **BMS history loaded into the DB** | ❌ Not yet — POC ran on JSON; needs ingestion to be query/UI-able |
| **Indication → MONDO ontology mapping (history)** | ❌ Not done — verbatim only |
| **Multi-page assembly (§3), corporate transactions (§4)** | ❌ Not built (BMS is single-source; no divestiture in window) |

### Data state — what's normalized & queryable

What we have for BMS history, and what each enables:

- **Phase — normalized throughout history ✅.** Only 11 distinct verbatim strings, all mapped to vocab
  codes (`Phase 1/2/3`, `…in Progress` → same code, `Registration (…)` → filed). So **"% of the
  pipeline in each phase per quarter" is directly derivable.**
- **Asset identity — resolved throughout history ✅** (deterministic + curated aliases; LLM-clustering
  pending). Powers add/advance/exit/partner change events.
- **Change events — computed + (in the engine) persisted to `change_event` ✅** (interval-dated,
  exit-classified, confirmed/provisional).
- **Therapeutic area — captured verbatim, not yet persisted/normalized ⚠️.** BMS's page groups by
  disease area and the extractor preserved it (`Disease Area`/`Therapeutic Area` field: "Oncology",
  "Solid Tumors", "Immunology", "Neuroscience", …). So **"% oncology per quarter" is answerable now,
  API-free** — measured: **~70% (2021) → ~55–60% (2024–25)**, BMS diversifying out of oncology. To
  make it a *queryable feature* needs: (a) load history into the DB, (b) capture the disease-area
  field into gold + normalize to the canonical therapeutic-area taxonomy (small, no API).
- **Indication → MONDO ontology — NOT done for history ❌** (372 noisy verbatim strings). Required for
  **cross-company, biology-aware adjacency** ("IL-23 programs in IBD *and adjacent GI indications*")
  — the deferred enrichment layer (OLS + LLM; LLM part API-blocked).

**Net:** phase-mix and therapeutic-area-mix over time are answerable from data in hand (the latter
after a small TA-normalization step); ontology-adjacency queries need the enrichment layer. None of
it is live in the explorer/API until BMS history is ingested into the DB.

## Context & purpose

The system today knows only each pharma pipeline's **current** state — every `program_version`
row is stamped with load-time, so the history table is flat. The goal is to reconstruct ~5 years of
each company's pipeline history from the Wayback Machine and, from it, produce the actual product:

**An investor-facing change-event feed** — "what changed in Company X's pipeline since last quarter":
phase advances, program additions, discontinuations, partner changes. The change-event stream *is*
the deliverable. SCD2 (`program_version`) is supporting infrastructure the events are derived from.

**Decisions (confirmed in review):**
- **Purpose:** investor change-event feed. **Horizon:** ~5 years (≈ back to 2021).
- **Cardinal sin = a phantom event.** Telling a subscriber a drug was discontinued when it was
  divested, renamed, or merely uncrawled that quarter is worse than missing a real one. Every guard
  below exists to prevent false events.
- **Rendering: standard replay URL + toolbar strip**, never `id_` (Gate 0).
- **SCD2 strategy: chronological rebuild from silver** (gold is derived/rebuildable; replay all
  extractions per logical pipeline in capture-date order). Idempotent — *conditioned on a durable
  decision store* (see §5).
- **Dates are intervals**, not points: `effective_date_min`/`effective_date_max`.
- **Provenance:** `captured_at` + `origin` columns on `snapshot`.
- **Rollout:** Gate 0 first, then a pilot of **GSK** (server-rendered, ground-truthable) **+ one
  client-rendered company** (e.g. Roche or JNJ) before generalizing to the ~20-company registry.

---

## Gate 0 — render fidelity (do this before any schema work)

**Why first:** v1 specified `web/<ts>id_/<original>`. A 10-minute test (run during review) on a 2020
Pfizer capture proved `id_` returns **un-rewritten** HTML — asset/script URLs like
`//s3.amazonaws.com/pfe_im/js/...s_code.js` and `//code.jquery.com/...` point at **live domains**.
Rendered, the page would execute today's JS against today's endpoints and yield **current data
stamped with a historical date** — contamination that passes every downstream guard. **Standard
replay** (no `id_`) rewrites every asset to `//web.archive.org/web/<ts>js_/...` (archived versions),
and uncaptured XHR returns a Wayback 404 → the page goes *empty*, not wrong (a safe, detectable
failure the bad-capture guard catches). It injects a toolbar, which we strip.

**The gate:** Playwright-render a **standard-replay** capture of a **client-rendered** pipeline page
(JNJ `.aspx` / Roche / Moderna SPA) and prove the DOM contains **period-correct** programs — e.g. a
drug known to be Phase 2 in 2021 shows Phase 2, not its 2026 phase. Concretely:
- Use `web/<ts>/<original>` (no `id_`); **block all non-`web.archive.org` network egress** at the
  Playwright layer so any live-domain leak fails loudly instead of contaminating.
- Strip the Wayback toolbar (`#wm-ipp-base`, `#donato`, injected `web-static.archive.org` nodes)
  before hashing/extraction.
- Eyeball-verify period-correctness against a known fact.

**If this fails on client-rendered pages, half of v2 is moot** — fall back to "only ingest captures
whose pipeline data is in server-rendered HTML or an archived PDF" and treat SPAs as gaps.

---

## The product: the change-event feed

`change_event` (new table) is the primary output, computed by the chronological replay at each
period transition. Event types: `program_added`, `program_discontinued`, `phase_changed`,
`status_changed`, `partner_added`, `partner_removed`, `indication_added`, `field_changed`
(RoA/territory/designation), `program_transferred`, `reappeared`. Each event carries:
- an **interval** (`effective_date_min` = last capture the prior state was seen;
  `effective_date_max` = first capture the change was observed) — the true date lies between, and
  an investor feed tolerates "discontinued between Q2 and Q3 2023";
- `status`: `provisional` | `confirmed` | `needs_review` — only `confirmed` events publish;
- provenance: `from_snapshot_id`, `to_snapshot_id`, `disposition`, optional `transaction_id`.

This is a new delivery surface in `pipeline_intel.search` + API + UI, on top of the existing facets.

---

## Continuity & change detection (the hard core)

The chronological replay **is** the diff engine: replaying periods oldest→newest, at each step we
hold `prev_set` and `cur_set` of programs and emit events from the delta. Three sub-problems must be
solved or the feed emits phantom events.

### 1. Stabilize identity across time — backed by a durable decision store (§5)

Identity drift (dev-code → brand rename; "NSCLC" → "Non-small cell lung cancer") makes one real
event look like a discontinuation **+** an addition — two phantoms from one truth.

- **Asset identity** = exact normalized name/synonym match (`_resolve_asset`, `upsert.py:75`),
  strengthened to **multi-signal** scoring — name + dev-code + target + indication + modality +
  sponsor, weighted (dev-code is a strong signal *when present*, not the sole one; many pages omit
  it). When a disappearance and an appearance co-occur and score as plausibly the same compound,
  run LLM adjudication — **but cache the verdict** (§5). Merge → record both names as durable
  synonyms; ambiguous → `review_queue`, never a silent disc+add.
- **Program identity** = (asset, indication, company), keyed on the **mapped ontology curie (MONDO)
  when available**, falling back to normalized label — so indication-wording drift doesn't fork one
  program into two. Historical indications are run through the existing mapper as part of backfill.

### 2. Disambiguate "absent" — the phantom-event guards

A program absent from a period has several causes; we never close on raw absence:
- **Page-not-captured guard (the #1 false-event source for a feed):** absence only counts if the
  *source page that would contain it* was actually captured in that period (see §3 assembly). If the
  containing page wasn't crawled, the program is **carried forward**, not closed.
- **Corporate-transaction check (§4):** before recording discontinuation, check whether the asset was
  divested/out-licensed/acquired — emit `program_transferred`, not `program_discontinued`.
- **Bad-capture guard:** non-200, too-short content, or >40% program drop vs prior period →
  quarantine the period, emit nothing, flag.
- **Rename check:** run §1 adjudication before any discontinuation.
- **Confirmation window:** discontinuation is `provisional` on first absence, `confirmed` after a
  second consecutive absence — **unless** the program is in an explicit "Removed/Discontinued"
  section (confirm immediately). Provisional events do not publish.
- **Reappearance:** a closed program that returns is reopened (new version) + a `reappeared` event;
  self-heals a one-off bad capture.

### 3. Two-track recording

- **`program_version` (SCD2)** opens a new row only on a change to `(phase_code, status)` — existing
  `_apply_scd2` tuple (`upsert.py:209`), now stamped with the period's `captured_at` interval.
- **`change_event`** captures everything finer-grained (incl. partner-set diffs — partnerships are
  asset-level today, `upsert.py:167`, and are diffed as a set across periods). Both are fully
  rebuildable from silver + the decision store.

---

## §3 Multi-page assembly layer (NEW — critical for a feed)

A company's pipeline is often spread across multiple pages/PDFs/detail views, and Wayback captures
them **on different days**. Treating one snapshot as the whole pipeline turns "this page wasn't
crawled this quarter" into a phantom mass-discontinuation.

- **Logical pipeline** = the set of `company_source` rows for a company (the schema already allows
  N sources/company). A **period** (a quarter) is assembled by gathering, for *each* source, its
  capture **nearest the quarter boundary within a tolerance window** (e.g. ±6 weeks).
- **Per-source completeness:** record which sources contributed to each period. The set-diff in §2
  runs **only over sources actually captured in that period**; programs whose source page is missing
  that quarter are carried forward, never diffed to "absent."
- A period with too few of its expected sources present is itself a bad capture → quarantine.

---

## §3a Assembly & replay semantics — the diff-engine spec (ONE-WAY DOOR)

`rebuild_history()` is built directly on these. Left implicit, they get resolved accidentally in
code and the accidental answers become load-bearing bugs. Tunable thresholds are flagged
**[two-way]** — set during the pilot; everything else is structural and fixed here.

**Definitions.** Logical source `S` = a pipeline page incl. its drifted-URL predecessors, captures
ordered by `captured_at`. Logical pipeline = the set of sources for a company. Period `P` = a
calendar quarter, anchored at `anchor(P)` (its first day) **[two-way: anchor position]**. Tolerance
`W` = max distance a capture may sit from an anchor to represent that period **[two-way]**.

- **R1 — Capture→period assignment (uniqueness & totality).** Each capture is assigned to exactly one
  period: `period(cap) = argmin_P |captured_at − anchor(P)|`. Within `(S,P)`, `cap(S,P)` = the capture
  nearest `anchor(P)`, tie-break earlier `captured_at`; if none, `cap(S,P)` is **absent**. So
  capture→(S,P) is deterministic and at most one capture represents each `(S,P)`. (Pick `W ≤ ½`
  quarter so windows can't overlap; argmin makes overlap impossible regardless.)
- **R2 — Per-source carry-forward (the core rule).** S's contribution at `P`: if `cap(S,P)` present →
  the programs extracted from it (S's contribution is *replaced*); if absent → S's contribution from
  the most recent earlier period where S *was* captured (*carried forward*). Assembled pipeline at
  `P` = union over all S. **A missing capture never removes programs**; only a present capture that
  *omits* a previously-present program can.
- **R3 — Per-source monotonicity.** Selected captures of a source are strictly increasing in
  `captured_at` (guaranteed by R1). A CDX timestamp that would invert order (clock/dedup anomaly) is
  dropped + flagged. State is carried from the previous *captured* period, never an uncaptured one.
- **R4 — "Consecutive" = consecutive captured periods of the owning source.** A program belongs to
  the source it appears on; absence is evaluated only in periods where that source was captured.
  First-absent = first captured period of S where it's gone (present in S's prior captured period);
  **confirmed discontinued** = absent in `N` consecutive captured periods of S (`N=2` **[two-way]**),
  no reappearance between; exception: an explicit "Removed/Discontinued" section confirms at first
  observation. Sparse captures correctly *slow* confirmation (annual captures ⇒ ~2yr to confirm);
  provisional events don't publish.
- **R5 — Date stamping (abutment invariant).** For a transition observed between S's captures at
  `P_prev` (old state last seen) and `P_cur` (new state first seen): change_event interval =
  `[captured_at(P_prev), captured_at(P_cur)]`; SCD2 closes the prior version with
  `valid_to = captured_at(P_cur)` and opens the new with `valid_from = captured_at(P_cur)`. **Versions
  abut** (`valid_from == prior valid_to`) — a contiguous chain, no gaps/overlaps, so point-in-time
  queries are unambiguous. Discontinuation dates come from the **first-absent** capture; **confirmation
  is a status flag** (provisional→confirmed at the Nth absence) and does **not** move the dates.
- **R6 — Cold-start baseline (no phantom adds).** The earliest assembled period is the **baseline**:
  its membership is initial state, *not* events. `program_added`/`program_discontinued` emit only from
  the second assembled period onward. Programs present before the window get
  `effective_date_min = null` (origin unknown), never a spurious "added".
- **R7 — Determinism.** Every stamp in R1–R6 derives purely from `captured_at` values + the decision
  store (§5) — no `now()`, no input-order dependence. This is what the determinism test asserts.

---

## §4 Corporate transactions (NEW)

Divestitures, out-licenses, acquisitions, and merged development efforts make many programs vanish
from one company at once — the opposite of the investment signal "discontinued," and exactly what
the 40% guard would either falsely quarantine or falsely mass-discontinue.

- Add dispositions: `transferred` | `out_licensed` | `divested` alongside
  active/discontinued/paused/unknown.
- **`corporate_transaction`** reference table (seeded from external signals — press releases,
  filings, a curated list — *not* inferable from the pipeline page alone): `transaction_id`, type,
  from_company, to_company, asset refs, effective date, source.
- `asset_id` **survives** ownership change (program is per-company; a transfer = close the program
  under the old company, open it under the new, linked by the shared `asset_id`). Emit
  `program_transferred` with the `transaction_id`.
- Replay heuristic: a simultaneous multi-program vanish that matches a known transaction → transfer
  events; an unmatched mass-vanish → quarantine + review (don't guess discontinuation).

---

## §5 Durable decision store (NEW — makes rebuilds deterministic)

v1 claimed idempotent rebuilds but put LLM adjudication *inside* the replay loop and deleted derived
history on rebuild — a contradiction (two replays could diverge; rebuild would erase accumulated
judgment, including `asset_synonym`, which is currently built during load).

- **Decision store lives outside derived tables and is never deleted by a rebuild:**
  - `asset_synonym` / `asset_alias` promoted to durable identity records.
  - `asset_merge_decision` (durable merge/split verdicts, human or LLM, with provenance).
  - `adjudication_cache` keyed by a **hash of the adjudication inputs** → the verdict, for **both**
    asset-merge **and** indication→MONDO mapping (item 2). The LLM/OLS is called **only on a cache
    miss**; identical inputs always return the cached verdict, so program identity (which keys on the
    curie) is stable across mapper versions.
  - Human `review_queue` resolutions persist here.
- **Rebuild deletes only `program_version` + `change_event`** for the logical pipeline, then replays,
  consulting the decision store before any LLM/human call. Result: deterministic, reproducible
  events; accumulated judgment is preserved across rebuilds.

---

## §6 URL-drift discovery (NEW — required to reach 5 years)

Measured CDX coverage (review): Pfizer ~2017→now, but **Roche/GSK/AbbVie only ~2022–2023 at their
*current* URL** — they restructured pipeline URLs, so 5-year depth requires the older URLs.

- CDX `matchType=prefix`/domain search to enumerate historical pipeline URLs per company; map the
  old URLs to the **same logical source** so history is continuous across the rename.
- Within a URL's lifespan, capture frequency is ample (20–90/yr) — quarterly sampling is feasible;
  the limiter is URL lifespan, which this step removes.

---

## Schema changes

`pipeline_intel/gold/models.py` + Alembic migration(s) (head is `25dbd5dfee09`):
- **`snapshot`**: `captured_at TIMESTAMPTZ NULL` (real-world content date; = `fetched_at` for live),
  `origin VARCHAR(16) DEFAULT 'live'` (`live`|`wayback`), **`extraction_quality_score NUMERIC NULL`**
  (item 3 — add the column NOW, populate from day one with a cheap per-extraction completeness proxy:
  fraction of required fields present, program-count vs. text-length, parse success; the *consuming*
  logic — distribution checks, redesign detection — comes during the pilot; retrofitting after 400
  extractions means re-extracting 400 captures); archive URL + raw 14-digit timestamp in `render_meta`.
- **`change_event`** (new): `event_id` PK, `program_id`/`asset_id`/`company_id` FKs, `event_type`,
  `disposition`, `field`, `old_value`/`new_value`, `effective_date_min`, `effective_date_max`,
  `from_snapshot_id`, `to_snapshot_id`, `transaction_id` FK NULL, `confidence`,
  `status` (`provisional`|`confirmed`|`needs_review`), `created_at`.
- **`corporate_transaction`** (new, §4) + a `program_version.disposition` extension.
- **Decision store** (§5): promote `asset_synonym`/`asset_alias`, add `asset_merge_decision`,
  `adjudication_cache`. **Item 2 — indication→MONDO mapping verdicts are part of the decision store
  from day one**: `indication_mapping` is durable (keyed by indication, untouched by rebuild) AND the
  mapper consults `adjudication_cache` keyed by a hash of (normalized label + context) before any
  OLS/LLM call, returning the pinned curie. Since program identity keys on the curie, pinning means a
  later mapper update **cannot silently re-fork programs** — re-mapping is an explicit cache
  invalidation, not a side effect. Without this, determinism is broken on day one.
- `program_version` keeps the `uq_program_version_current` partial unique index (replay ends each
  program with exactly one open row, or zero if discontinued/transferred).

---

## Ingestion (`pipeline_intel/wayback/`, reusing `ingest/`)

- **`cdx.py`** — per-source CDX query + §6 URL-drift discovery; **quarter sampling** (capture nearest
  each quarter boundary, last ~5yr); reuse `httpx` + `tenacity` as `ontology/ols_client.py` does.
- **`backfill.py`** — for each sampled capture: **standard replay URL** `web/<ts>/<original>`,
  Playwright render with **non-archive egress blocked** + **toolbar stripped**, then
  `write_snapshot(... captured_at, origin='wayback')` → `extract`.
- **`ingest/render.py`** — skip the *company* robots check for `web.archive.org` hosts (we fetch
  archive.org, not the company); keep the rate-limit delay (Wayback throttles hard).
- **`ingest/snapshot.py`** — `write_snapshot()` gains `captured_at`/`origin`; `_artifact_prefix`
  (`snapshot.py:22`) uses `captured_at.date()`; `latest_hash` orders by `captured_at`. **Always
  persist raw artifacts** — drop the artifact-less "unchanged" path for backfill so re-extraction
  (after extractor improvements) never refetches and re-triggers Wayback rate-limiting.

---

## Load (`pipeline_intel/gold/upsert.py`)

- `_apply_scd2` gains an `effective_date` (interval) param; stamp `valid_from`/`valid_to` from
  `captured_at`, not `now()`.
- **`rebuild_history(s, company_id)`**: assemble periods (§3) → sort by `captured_at` → delete only
  `program_version` + `change_event` for the logical pipeline → replay each period through
  resolution (consulting the decision store §5) + §2 guards + §4 transaction check → emit SCD2 rows
  + change events with min/max intervals. Reuse a hash-unchanged capture's prior extraction.
- `_resolve_asset`/`_resolve_indication` get the §1 multi-signal + curie-keyed + decision-store hooks.

---

## Enrichment, UI/API, CLI

- **Enrichment:** re-run `enrich` + closure rebuild + `classify-ta` after backfill (new historical
  indications/assets need MONDO/TA); curie-keyed program identity depends on it.
- **UI/API:** present-day view **unchanged** — `facets.py:_current_program_query` filters
  `valid_to IS NULL`. Switch the surfaced as-of date from `Snapshot.fetched_at` (`facets.py:57`) to
  `captured_at`. **New surface: the change-event feed** (the product) — per-company, per-quarter
  event stream with provenance and confirmed/provisional state.
- **CLI** (Typer, `cli.py`): `discover-urls`, `backfill-wayback --company X [--since YYYY]
  [--per-year 4] [--dry-run]`, `rebuild-history --company X [--all]`; track as `job_run` rows.

---

## Rollout

0. **Gate 0** render-fidelity test (above) — go/no-go on client-rendered pages.
1. **Pilot: GSK** (server-rendered, validatable against their spreadsheet for the *latest* capture)
   **+ one client-rendered company** (Roche/JNJ) end-to-end: discover URLs → sample → render →
   extract → assemble → `rebuild-history` → re-enrich → inspect the event feed.
2. Generalize to the ~20-company registry once the pilot event feed reads correctly.

---

## Verification

- **Gate 0**: documented period-correct render on a client-rendered page (the decisive test).
- **Synthetic replay unit tests** (`tests/`): phase advance; clean discontinuation; reappearance
  after a bad capture; dev-code→brand rename → **one asset, no phantom disc/add**; indication-wording
  change → one program (via curie); a **missing-source period** → carried forward, **no** false
  discontinuation; a **divestiture** matching a `corporate_transaction` → `program_transferred`, not
  discontinued.
- **Determinism test:** run `rebuild-history` twice → byte-identical `change_event` set (proves the
  decision store + cache work).
- **Event validation:** confirmed events for the pilot reconcile with **known historical events**
  (a press-released discontinuation, an investor-deck Phase 3 start) — the feed's ground truth.
- **Current-view regression:** `valid_to IS NULL` program count = present set minus genuinely
  discontinued/transferred (no stale pollution, no missing current programs).

---

## Risks / open items

- **Render fidelity on SPAs** (highest): mitigated by standard-replay + egress-block + Gate 0;
  residual SPA captures that archived without their data become gaps, not errors.
- **Transfer detection needs external data** (#4): pipeline pages rarely state a divestiture;
  `corporate_transaction` must be seeded from filings/press releases. Unmatched mass-vanish →
  review, never auto-discontinue.
- **Review-queue ownership/throughput** (open): renames, quarantines, provisional discontinuations,
  and transfer-confirmations all route to a human gate before events publish. The M2 review UI is
  the tooling, but **owner + SLA + throughput are unestimated** — at scale this gate, not compute, is
  the likely bottleneck. The §5 decision store is the main lever to keep its volume bounded.
- **Historical ground truth** (#12): GSK's spreadsheet validates only the *latest* capture; the feed
  is validated against *known events*, which still needs a handful of period-correct anchors per
  pilot company (archived annual reports / investor decks).
- **Cost:** ~5yr × ~4/yr × ~20 companies ≈ ~400 captures of extraction — bounded; GSK pilot calibrates.
- **Tuning (defer to pilot):** capture-window tolerance, confirmation-window length, bad-capture
  drop threshold.

---

## One-way vs two-way doors (sequencing doctrine)

Schemas and replay semantics are **one-way doors** — resolve them before code. Guards, thresholds,
and publishing policy are **two-way doors** — walk through them during the pilot.

- **Blocking (resolved before code):** the §3a assembly/replay semantics (the diff-engine spec);
  indication→MONDO verdicts in the decision store from day one (item 2, §5); the
  `extraction_quality_score` column on `snapshot` populated from day one (item 3) — logic deferred,
  *column* not.
- **Week one, not a design blocker:** the **vertical slice** — one GSK source through
  render → extract → naive diff, *before* any guard machinery. A day's work; it's the only early read
  on whether historical extraction quality is ~95% or ~60%, which decides how much of item 3's
  consuming machinery is actually needed.
- **Explicitly deferred (two-way):** forward/live-path reconciliation — answer before the **feed
  surface** (build step 7), not before ingestion/replay. Validation-precision target — a **pilot exit
  criterion**, define before generalize sign-off. Partner-event gating + the egress allowlist detail —
  implementation-time.

## Build order (re-ranked for a change-event feed)

*(Pilot was run on **BMS** rather than GSK — single-source page with a captured therapeutic-area
column and an authoritative current spreadsheet, ideal for a first end-to-end pass.)*

0. ✅ **Vertical slice** — BMS render → extract → naive diff (proved the noise; motivated the layers).
1. ✅ **Gate 0** render-fidelity — passed on BMS (22/22, zero leaks, period-correct).
2. ✅ **§3a spec** — written here and implemented in `detect.py`.
3. ✅ **Schema** — `captured_at`/`origin`/`extraction_quality_score`, `change_event`, `asset_alias`
   decision store. ⬜ *Remaining:* indication-mapping verdicts table, `corporate_transaction`.
4. ⬜ **URL-drift discovery (§6)** — not needed for BMS (current URL spans the window); required to
   generalize to companies that restructured URLs.
5. 🟡 **Standard-replay ingestion + always-store-raw** — done as POC scripts; ⬜ not yet wired into
   `pipeline_intel.ingest` / the registry as a first-class `origin=wayback` path, and BMS captures
   are not yet ingested into the DB.
6. ✅ **Chronological replay + §2 guards + decision store** — `detect.py` + `rebuild.py` (tested).
   🟡 Assembly layer (§3) deferred (BMS is single-source).
7. ⬜ **Transactions (§4) + forward/live reconciliation + the feed surface** (API/UI).
8. 🟡 **Pilot done (BMS); ⬜ define precision target + external approval signal; then generalize.**

### Immediate next steps (in priority order)

1. **Ingest BMS history into the DB** (snapshots with `captured_at`/`origin=wayback` + extractions +
   gold), then `pipeline rebuild-history --company "Bristol Myers Squibb"` → the feed is live in the DB.
2. **Capture + normalize therapeutic area** into gold (the disease-area field is already extracted) →
   unlocks "% oncology / % by TA per quarter" as a real query. API-free.
3. **External approval signal** (FDA/press) to split ambiguous exits into approved-graduated vs
   discontinued — the one thing the pipeline page can't do alone.
4. **(When API limit lifts 2026-07-01)** run `merge_assets.py` → populate `asset_alias` from LLM
   clustering; re-extract the 2 quarantined quarters; run indication → MONDO mapping on history.
