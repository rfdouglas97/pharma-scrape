# Block 1 — Missing ingestion primitives

## Context
The repo has two layers: a solid M0–M4 medallion pipeline (bronze→silver→gold, ontology,
search, API, UI) with real data for ~6 companies, and a "Codex factory" (batch state
machine, model routing, LLM-as-judge QA) that is wired + unit-tested but barely run
(2 batch executions; 18/22 companies stuck at `unverified_source`).

The factory can't scale because the **ingestion primitives underneath it are stubs**:
- `render.py` only does HTML + screenshot via Playwright. Downloadable pipeline files
  (PDF/XLSX/CSV) — often the *cleanest* source (e.g. GSK) — are never fetched or parsed.
  `Snapshot.pdf_keys` exists but is always empty.
- `source_discovery.py` classifies URLs by extension and ranks them, but nothing fetches
  the files, and the page is never classified (`html_table`/`js_cards`/`image_page` are
  defined but never assigned), so "figure out the extraction method" never closes.
- `repair_mode` is set by `repair_company()` but `render.py` never reads it → the repair
  loop doesn't actually re-render differently.

Block 1 builds the three tractable, high-leverage primitives. The image-extraction
subsystem (two-pass Claude vision) is **Block 2**, after we validate Block 1 on real data.

## Scope (this block)
1. **Document ingestion** — fetch + parse PDF / XLSX / CSV into clean text, feed the
   existing text-only LLM extraction path.
2. **Real source-type detection** — sniff doc-vs-page before fetch; classify rendered
   pages after render; persist `source_type` so model routing (already keyed off it)
   becomes real.
3. **Wire `repair_mode`** — make the repair flag actually escalate rendering.

No DB migration needed: doc artifacts reuse `Snapshot.pdf_keys` + `render_meta` (JSONB),
and `CompanySource.source_type` already exists.

## Dependencies to add (`uv add`)
- `openpyxl` (xlsx), `pdfplumber` (pdf text + tables). CSV uses stdlib.

## New modules
- `pipeline_intel/ingest/fetch_doc.py` — `fetch_document(url) -> DocFetch(url, http_status,
  content_type, raw_bytes, ext)`. httpx (existing dep), crawler UA, size cap, robots-respecting.
- `pipeline_intel/ingest/parse_doc.py` — `parse_document(raw_bytes, content_type|ext) ->
  ParsedDoc(text, tables)`. CSV→table text; XLSX→per-sheet markdown tables (openpyxl);
  PDF→text + `extract_tables()` rendered as markdown (pdfplumber). `text` is used for
  content-hash change detection AND as `page_text` for extraction.
- `pipeline_intel/ingest/classify.py` —
  - `sniff_url_type(url, content_type) -> 'csv_doc'|'xlsx_doc'|'pdf_doc'|None` (pre-fetch).
  - `classify_rendered_page(html, text, pipeline_image_urls) -> 'image_page'|'html_table'|
    'js_cards'|'pipeline_page'` (post-render heuristic).

## Integration changes
- `ingest/snapshot.py` — add `write_doc_snapshot(...)`: hash from parsed text; store raw
  doc bytes (`{prefix}/source{ext}`) + `page.txt`; set `pdf_keys` for pdf; `render_meta`
  carries `text_key`, `doc_key`, `doc_content_type`, `source_kind="document"`. No screenshot/html.
- `ingest/run.py` — in the per-source loop, branch: doc-type source (by `src.source_type`
  or a cheap content-type sniff) → `fetch_document` + `write_doc_snapshot`; else `render` +
  `write_snapshot`, then `classify_rendered_page` and persist detected `source_type` onto the
  `CompanySource` (when null/generic) + `render_meta`. Robots check applies to both paths.
- `extract/extractor.py` — generalize the `extract_snapshot` guard (currently skips unless
  `snap.html_key`). Skip only if unchanged or no extractable artifact (no text_key/html/shots).
  Doc snapshots have no html → deterministic returns None → existing **text-only** LLM path
  (`run_extraction` with `screenshots=[]`) runs on the parsed text. Tag `usage.input_mode="document"`.
- `ingest/render.py` — when `render_config.repair_mode`: auto-scroll in steps (trigger lazy
  load), apply generic cookie-dismiss + expand selectors, bump `wait_ms`/timeout, force
  `full_page`. Pure helper `repair_render_config(cfg)` so it's unit-testable.

## Reuse (don't reinvent)
- `run_extraction(..., screenshots=[])` already does text-only structured extraction.
- `robots_allows`, `get_storage`, `content_hash`, `_artifact_prefix` patterns.
- `route_for_company_source` already routes on `source.source_type` — just needs it populated.

## Tests
- `tests/test_parse_doc.py` — CSV string + xlsx built in-test (openpyxl) → expected table text;
  markdown-table renderer on synthetic tables; tiny checked-in PDF fixture for pdfplumber wiring.
- `tests/test_classify.py` — `classify_rendered_page` on synthetic html (image-only, table,
  cards, generic); `sniff_url_type` by ext/content-type.
- `tests/test_render_repair.py` — `repair_render_config` is a pure function: asserts escalation.
- `tests/test_fetch_doc.py` — ext/content-type mapping + size-cap logic (no live network).

## Verification (end-to-end)
- `uv run ruff check pipeline_intel/` and `uv run pytest -q` green.
- Add GSK's downloadable pipeline file as a source (`pipeline source-discover --company GSK
  --persist`, or insert the known xlsx URL), then `pipeline run --company GSK --extract`:
  confirm a doc snapshot + text-only extraction whose assets reconcile with GSK gold.
- Repeat on one PDF-publishing company.
- Re-render an image-only page (Krystal/Moderna) and confirm `source_type` is detected as
  `image_page` (so routing escalates) — full image extraction is Block 2.

## Out of scope (Block 2)
Two-pass Claude vision image extraction (VisualEvidenceRow → normalize → vision judge),
validated on Krystal (11 rows) / Moderna.
