# BMS 5-Year Pipeline History — Morning Summary

**Status: built end-to-end and validated.** Start here, then see `HISTORY_REPORT.md` for the full
event list. Everything lives in `artifacts/bms_history/` (gitignored). No DB or schema was touched.

## What ran (overnight)

A complete, scoped version of the Wayback backfill plan, on BMS as the test subject:

1. **CDX discovery** — BMS's current pipeline URL covers all of 2021–2026 (no URL-drift gap in window).
2. **22 quarterly captures** (2021Q1–2026Q2) selected by the §3a nearest-anchor rule, all within 60d.
3. **Rendering** — standard Wayback replay, **all non-archive egress blocked**, toolbar stripped.
   **22/22 rendered, zero live-domain leak attempts** (Gate 0 holds at scale).
4. **Extraction** — Opus 4.8 vision on all 22 (the older captures render phase as a *visual bar chart*,
   so vision is mandatory; a text scraper would get 0% phase).
5. **Replay** (`build_history.py`) with the layers the naive diff proved necessary:
   identity resolution + LLM-style rename-merge + phase normalization + §3a semantics
   (cold-start baseline, 2-quarter confirmation, interval dates, bad-capture guard).

## Headline finding (the important one)

**"Disappeared from the pipeline page" ≠ "discontinued."** A drug leaving the page is three-way
ambiguous: **approved & graduated** (the page only shows investigational assets), **discontinued**, or
**renamed**. Known BMS approvals (SOTYKTU '22, CAMZYOS '22, AUGTYRO '23, COBENFY '24) do **not** show as
"Approved" — they just vanish. **So this source gives trustworthy *additions* and *in-pipeline phase
advances*, but *exits* need an external approval/press signal to classify** (this generalizes plan §4,
and is the single most important design implication for the investor feed).

## What's trustworthy vs not

| Signal | Count | Trust |
|---|---|---|
| New assets entering pipeline | 73 | **High** |
| In-pipeline phase advances | 38 | **High** |
| Partner changes | 31 | Medium-high (real M&A handoffs; some extraction flicker) |
| Assets that *left* the pipeline | 102 | **Ambiguous** — split by exit phase, needs external data to classify |

Exit split: 21 left from Phase 3 (approval **or** failure — mixed), 78 left from Phase 1/2 (usually
real discontinuations), 0 from "Filed" (BMS collapses Filed into Phase 3 until ~2026).

## Validation against known facts (spot-checks that passed)

- **KRAZATI** enters at Phase 3 in 2024Q2 — matches the Mirati acquisition closing Jan 2024. ✅
- **RYZ101** enters 2024Q3 — RayzeBio acquisition. ✅  **pumitamig/BNT327** enters 2025Q4 — BioNTech deal. ✅
- **milvexian** Phase 2→3 in 2023; **iberdomide/golcadomide** Ph2→3 — all real. ✅
- Partner feed caught real handoffs: **REBLOZYL Acceleron→Merck**, **IDHIFA Agios→Servier**,
  **ABECMA bluebird→2seventy** (genuine M&A, not noise). ✅
- **SOTYKTU/COBENFY correctly stay present** (still developed for new indications) — *not* falsely
  flagged as discontinued. ✅
- Real discontinuations land in the Phase 1/2 exit bucket: pegbelfermin, branebrutinib, danicamtiv,
  CC-90009, Anti-TIGIT. ✅

## Noise reduction (why the layers matter)

Naive churn (raw names, no identity/phase-norm): **656** add/remove events →
after identity + rename-merge + phase-norm: **260** events. **~60% of the naive churn was noise**
(name-variant drift like `liso-cel`→`BREYANZI`, and phase-wording like `Phase 3`→`Phase 3 in Progress`).

## Known limitations / caveats

- **Indication-level (asset×indication) feed is deferred.** Indication wording drifts heavily (OPDIVO
  alone: 93 distinct indication strings), so a clean program-level feed needs ontology (MONDO) mapping —
  the next layer. This is **asset-level** history.
- **Rename-merge is hand-curated** (`curated_aliases.json`) because the LLM clustering step
  (`merge_assets.py`, built and ready) was **blocked overnight by an API usage limit** (regains
  2026-07-01). Residual rename noise remains in the exit list (~25 of 102 exits have a nearby addition).
- **2 of 22 captures quarantined** (2022Q2, 2025Q3) — anomalous low extractions (one was an extraction
  miss, one a partial render). The bad-capture guard carried them forward; no false events. Re-extraction
  was attempted but also hit the API limit.
- **2021 captures over-segment** (chart format) vs BMS's stated "50+ compounds", so the 85→50 asset
  decline is partly real streamlining, partly format normalization.
- Some partner-change **flicker** remains (a partner footnote present in some captures, absent in others).

## Files

- `MORNING_SUMMARY.md` (this) · `HISTORY_REPORT.md` (full event list) · `stats.json`
- `change_events.json` (260 events) · `timeline.json` (per-asset phase trajectory)
- `selected_quarters.json` · `renders/` (22 txt+png) · `extractions/` (22 json)
- Code: `render_all.py`, `extract_parallel.py`, `build_history.py`, `merge_assets.py`, `curated_aliases.json`

## Suggested next steps

1. **Run `merge_assets.py`** once API access returns → replaces the hand-curated map with LLM clustering
   (and becomes the durable decision store per plan §5).
2. **Add an external approval signal** (FDA/press) to disambiguate the 102 exits into approved-vs-discontinued.
3. **Re-extract the 2 quarantined quarters** when the API is back.
4. If the asset-level history looks right, **productionize into the schema** (`captured_at`/`change_event`
   tables) so it lands in the explorer — the extractions are reusable, nothing is wasted.
