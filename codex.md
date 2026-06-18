# Codex Plan: Scalable Pharma Pipeline Scrape Factory

## Summary
Build the live pipeline database stream into an autonomous, batchable data factory: feed in company/ticker records, discover or use official pipeline sources, render and extract assets, run automated LLM-as-judge QA, and only publish companies that pass objective quality gates. Human review should not be required for normal operation; failures become agent-readable repair tasks and retry loops.

The core architectural rule is now explicit: company-specific extraction code is not scalable and should not be used for production asset rows. Company-specific configuration is acceptable for source URLs, render behavior, expected counts, and source ranking. Extraction logic should be artifact-specific: HTML/table/card extraction, document extraction, visual/image extraction, and LLM normalization/QA.

## Current Status
- Root-level `codex.md` exists as the durable project plan.
- Batch workflow scaffolding exists around ingest/extract/QA/load.
- Company/source state fields and QA tracking fields have been added via migrations.
- Source discovery exists for pipeline links and downloadable source candidates.
- Model routing exists for cheaper/default models versus higher-intelligence escalation.
- Anthropic Message Batch scaffolding exists for lower-cost batch extraction.
- Deterministic extraction exists for structured HTML/card pages where the page exposes usable DOM data.
- MGTX / MeiraGTx validated the structured HTML/card path: 17 programs extracted, QA passed, loaded to gold.
- Krystal Biotech exposed an important gap: its official pipeline is image-backed. Generic image artifact discovery/storage now exists, but image extraction quality still needs a dedicated subsystem before scale.

## Key Changes Implemented
- Added batch workflow commands around the existing ingest/extract/load path.
- Added explicit company/source progress states: `unverified_source`, `render_ok`, `extraction_ok`, `qa_passed`, `loaded_gold`, `needs_repair`, `failed`.
- Added source preference logic: official `xlsx/csv` pipeline files first, then official PDF tables, then HTML tables/cards, then image-heavy pages.
- Added a checker component in `pipeline_intel/quality` that combines deterministic safeguards with an LLM judge.
- Added QA fields to tracking tables: `qa_status`, `qa_confidence`, `qa_report`, `expected_count`, `observed_count`, `repair_attempts`.
- Added CLI commands for batch, QA, repair, source discovery, model-batch extraction, and cost estimation.
- Preserved immutable bronze artifacts and silver extraction output so QA and repairs are reproducible.
- Added generic linked-pipeline-image discovery in render metadata.
- Added bronze persistence for linked pipeline image artifacts.
- Updated snapshot hashing so image-backed pages include linked image bytes/identity in change detection.
- Updated extraction payload loading so saved linked pipeline images are sent to the generic vision extractor alongside screenshots.
- Removed Krystal-specific production row extraction. Krystal should be handled through generic visual extraction, not company-specific code.

## Architecture Boundary
Allowed company-specific configuration:
- Official pipeline/source URL.
- Source type and source rank.
- Render config such as waits, scroll, accordions, cookie dismissal, and pagination hints.
- Known expected count when disclosed by the company or known from trusted ground truth.

Not allowed for scalable production:
- Hardcoded company-specific asset/program rows.
- Company-specific parser branches such as `if company == Krystal`.
- Silent gold loading from unsupported or partially supported evidence.

Preferred production flow:
```text
Company / ticker
  -> source discovery and source ranking
  -> ingest/render official source
  -> preserve bronze artifacts
       HTML
       visible text
       screenshots
       linked images
       PDFs
       XLSX/CSV
  -> classify artifact evidence
  -> run artifact-specific extractor
       HTML/table/card extractor
       document extractor
       visual/image extractor
       LLM fallback extractor
  -> normalize to canonical asset/program schema
  -> QA judge compares final JSON back to source evidence
  -> load to gold only if gated pass
```

## Visual/Image Extraction Lesson
Krystal Biotech showed that image-backed pharma pipelines are a first-class source type, not an edge case. The current generic path can now discover and store linked pipeline images, and the generic model path extracted the correct Krystal count of 11 programs. However, the extraction missed or degraded important fields such as target, modality, and one phase. That is not acceptable for an investing-grade pipeline database.

The image extraction layer should therefore become a dedicated subsystem rather than a side effect of the normal LLM extractor.

Target visual architecture:
```text
Pipeline image / screenshot
  -> OCR and layout extraction
  -> visual table/chart reconstruction
  -> evidence rows with field-level provenance/confidence
  -> LLM schema normalization
  -> LLM-as-judge QA against the original image and evidence rows
  -> gold load only if supported
```

Candidate external tools for visual extraction:
- Claude/GPT/Gemini vision for semantic chart understanding and phase-bar reasoning.
- Google Document AI, Azure Document Intelligence, or AWS Textract for OCR/layout/table extraction.
- Tesseract/EasyOCR/PaddleOCR as cheap local OCR first-pass options.
- Firecrawl as an optional ingestion/markdown helper, not the primary solution for image-only charts.

Current recommendation:
- Do not rely on Firecrawl alone for this problem.
- Add Firecrawl only as an optional ingestion provider that saves markdown/html evidence alongside Playwright artifacts.
- Prioritize a generic visual evidence extractor that reads image-backed pipeline charts into intermediate evidence rows before schema normalization.
- Use Krystal as the canonical regression fixture for image-backed pipeline extraction.

## QA / Checker Design
- Inputs: company metadata, source URL/type, rendered text, screenshot tiles, linked files/images, extraction JSON, previous scrape metrics.
- Checker output: `pass`, `warn`, or `fail`, plus confidence, missing assets, extra assets, suspicious fields, count mismatches, and recommended repair action.
- Required checks:
  - Asset/program counts reconcile with visible page counts, table rows, section totals, or company-stated totals when available.
  - Extracted asset names, phases, indications, targets/MoA, modality, partners, and active/discontinued status are supported by source evidence.
  - Pipeline changes, removed, discontinued, and historical-change sections are not mixed into active pipeline counts.
  - Large unexplained count drops versus the last successful scrape fail gated publish.
  - For image-backed pages, target/modality/phase fields must be supported by visible image/OCR/layout evidence or explicitly marked null/uncertain.
- Repair loop:
  - Retry render with scroll, waits, accordions, cookie dismissal, and source-specific config.
  - Prefer discovered downloadable files when rendered page evidence is weak.
  - Run focused re-extraction for missing sections identified by the judge.
  - For image-backed pages, retry with OCR/layout extraction and stronger vision model before failing.
  - Keep failing companies in `needs_repair` with machine-readable reasons.

## Interfaces / Data
- Persistent QA fields: `qa_status`, `qa_confidence`, `qa_report`, `expected_count`, `observed_count`, `repair_attempts`.
- Source metadata/config: `source_type`, `preferred_source_rank`, `known_expected_count`, `render_config`.
- Bronze artifact metadata now includes linked pipeline image URLs and stored image keys when discovered.
- CLI commands:
  - `pipeline batch --limit 10`
  - `pipeline qa --company TICKER_OR_NAME`
  - `pipeline repair --company TICKER_OR_NAME`
  - `pipeline source-discover --company TICKER_OR_NAME`
  - `pipeline model-batch submit-extractions`
  - `pipeline model-batch status`
  - `pipeline model-batch collect-extractions`
  - `pipeline cost-estimate`

## Test Plan
- Unit-test QA verdict parsing and pass/warn/fail threshold behavior.
- Fixture-test checker behavior on existing GSK, BMS, Moderna, Lilly, MGTX, and Krystal examples.
- Add regression cases for count mismatch, missed section, extra removed assets, bad active/discontinued classification, and missing downloadable file preference.
- Add image-backed pipeline regression for Krystal:
  - Detect linked pipeline image artifact.
  - Persist linked image as bronze evidence.
  - Extract 11 rows through generic visual extraction.
  - Capture targets, modality, phases, indications, and approved/commercial status correctly.
  - Fail QA if image evidence does not support the final JSON.
- Add CLI tests for batch selection, state transitions, gated publish, and retry exhaustion.
- Keep existing golden/eval harness as a model-regression tool, but do not require human labeling for routine company completion.

## Immediate Next Milestone
Build the generic visual evidence extractor before scaling beyond a handful of companies.

Milestone scope:
- Create an intermediate `VisualEvidenceRow` representation with fields like asset/program, target, indication, phase, modality, status, row/column evidence, confidence, and source artifact key.
- Add an OCR/layout pass for linked images and screenshot tiles.
- Add a vision prompt that first transcribes/reconstructs the chart row-by-row before schema normalization.
- Add a second QA prompt that compares normalized JSON back to the visual evidence and original image.
- Run Krystal through this path until it matches the trusted 11-row ground truth without company-specific extraction code.
- Only then proceed to batches of 10 companies.

## Assumptions
- Autonomous LLM-as-judge QA is the default path; human review is optional and exceptional.
- The first implementation should optimize for correctness and provenance over throughput.
- Batch size starts at 10 companies so failures are easy to inspect and repair before scaling toward 600.
- Companies that fail QA are not published to gold unless an explicit override path is later added.
- Smaller companies should be cheaper because they have fewer assets and simpler sources, but cost control must not override QA gates.
