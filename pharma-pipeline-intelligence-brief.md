# Pharma Pipeline Intelligence Database — Project Brief & Scope

## 1. Overview

Build a production-grade, continuously-maintainable database of the development pipelines of the world's largest pharmaceutical companies, sourced by scraping each company's public pipeline disclosures. The system normalizes heterogeneous, free-text pipeline data into a clean, ID-anchored schema, enriches it against established biomedical ontologies, and exposes it for intelligent search by investors — across web UI, API, and MCP surfaces.

This document defines **scope and requirements (the "what")**. Architectural recommendations are flagged as such and are intended as starting points for the downstream architecture/coding engine, not mandates. The goal is to give that engine enough constraint to produce a coherent, opinionated design while leaving room for it to choose the stack.

**Primary user:** institutional and individual investors performing thematic and asset-level research (e.g., "show me all clinical-stage assets targeting a given mechanism across an indication area, including adjacent indications").

---

## 2. Goals & Non-Goals

### Goals (Phase 1)
- Scrape and structure the pipelines of the ~200 largest pharma companies, with the schema and infrastructure designed to scale to **500+ companies** without re-architecture.
- Capture, per asset, a defined core metadata set plus any additional fields a given company discloses.
- Normalize key categorical fields (phase, modality, etc.) to controlled vocabularies.
- Anchor every entity with stable internal IDs (company, asset, program) and map indications/targets to external ontology IDs for cross-company comparability and adjacency-aware search.
- Support intelligent, biology-aware search that surfaces indication adjacencies — not pure keyword/regex matching.

### Goals (Phase 2 — design for now, build later)
- **Change tracking / provenance:** detect and record when assets change phase, when programs are added, discontinued, or continued, and maintain a full history of each company's pipeline over time.
- **Delivery surfaces:** web UI front end, plus programmatic access via REST/GraphQL API and an MCP server.

### Non-Goals (explicitly out of scope for now)
- No primary-source aggregation beyond company-disclosed pipeline pages in Phase 1 (e.g., ClinicalTrials.gov, conference abstracts, patents, press releases). These are candidate future enrichment sources, not Phase 1 inputs.
- No proprietary/paywalled data ingestion.
- No investment recommendations or scoring — this is a data product, not an advisory product.

---

## 3. Source Data & Coverage

- **Targets:** the pipeline section of each company's corporate or R&D website.
- **Company universe:** start with the largest pharma/biotech by market cap and/or pipeline depth; maintain the company list as managed, versioned configuration (not hard-coded), since the set will grow and change.
- **Core metadata to capture per asset:**
  - Asset name (and any synonyms / development codes shown)
  - Phase
  - Indication
  - Target
  - Modality
  - Partnerships / collaborators
  - **Plus any additional fields the company discloses** (e.g., mechanism of action, route of administration, lead/originator, geography/territory, expected milestones, designation status). The schema must accommodate company-specific fields without losing them.

### Extraction reality (important design constraint)
Pipeline pages are **highly heterogeneous** and are the single biggest execution risk. Expect: static HTML tables, JavaScript-rendered interactive dashboards, expandable accordions, downloadable PDFs, and — frequently — pipeline charts published as **images** with no underlying machine-readable data. A brittle per-site CSS-selector scraper will not generalize and will be expensive to maintain.

**Recommendation:** favor a generalized extraction pipeline — fetch → render (headless browser) → extract structured JSON against the canonical schema using an LLM (with vision capability for image/PDF-based pipeline charts) — with lightweight per-site configuration only where needed. This is more robust to layout changes than hand-written parsers and aligns with the "one-shot at scale" ambition. Always preserve the **raw source artifact** (rendered HTML/PDF/image) alongside the extracted record for auditability and re-extraction.

---

## 4. Data Model & Schema

The schema must be data-engineering-grade: ID-anchored, normalized, and provenance-aware from day one.

### Key modeling principle: program-level phase
**Phase belongs to the (asset × indication) pair, not to the asset.** A single drug is routinely in different phases across different indications. The atomic unit with a phase, target indication, and status is therefore a **program** (asset-in-indication), not the asset itself. Modeling this correctly upfront avoids a painful migration later.

### Candidate core entities
- **`company`** — internal `company_id`, name, ticker/identifiers, parent/subsidiary relationships, source URL(s).
- **`asset`** — internal `asset_id`, primary name, synonyms/dev codes, modality (normalized), originator vs. current sponsor, mechanism/target reference.
- **`program`** — `program_id` = the (asset × indication) unit; carries phase (normalized), status, sponsoring company, and partnership context.
- **`indication`** — normalized label + external ontology ID + hierarchy (see §5).
- **`target`** — normalized to gene/protein identifiers (see §5).
- **`partnership`** — collaborator(s), deal/collaboration type, territory/geography, role.
- **`source_record` / `snapshot`** — raw scraped artifact, source URL, fetch timestamp, content hash, and extraction metadata (model/version) — the provenance backbone.

### Normalization
- Maintain controlled vocabularies for the categorical fields that need cross-company comparability — at minimum **phase** (e.g., Preclinical, Phase 1, Phase 1/2, Phase 2, Phase 3, Filed/Registration, Approved/Marketed, Discontinued) and **modality** (e.g., small molecule, mAb, ADC, bispecific, cell therapy, gene therapy, RNA/oligonucleotide, vaccine, etc.).
- Preserve the **original verbatim value** alongside the normalized value for every normalized field. Never destroy the source text.
- Treat the controlled vocabularies as versioned, extensible configuration with an explicit "unmapped / needs review" state rather than silently dropping or guessing.

---

## 5. Ontology Enrichment

Anchor indications and targets to established biomedical ontologies so that search can reason about biological relationships rather than string matches. This is what enables indication-adjacency search.

**Recommended external anchors (all EMBL-EBI / open ecosystem):**
- **Indications/diseases:** map to **EFO** (Experimental Factor Ontology) and/or **MONDO**, resolved via the **EMBL-EBI Ontology Lookup Service (OLS) API**. These provide stable disease IDs *and* an `is-a` hierarchy (e.g., NSCLC → lung carcinoma → lung cancer → thoracic neoplasm), which is the structured form of "indication adjacency."
- **Targets:** normalize to gene/protein identifiers (Ensembl / UniProt / approved symbols).
- **Drugs / mechanisms:** **ChEMBL** for compound and mechanism-of-action data.
- **Strongly consider the Open Targets Platform** (EBI + Wellcome Sanger, GraphQL API) as a single integrated source linking targets ↔ diseases (EFO) ↔ drugs (ChEMBL), with target–disease association evidence. For an investor-facing pipeline tool this is likely the highest-leverage single enrichment source and is worth evaluating early.

Each external mapping should be stored with its source, ID, confidence, and a review state, so low-confidence auto-mappings can be flagged rather than trusted blindly.

---

## 6. Search Architecture

The requirement is **biology-aware search** that captures indication adjacencies — not regex or keyword-only. Investors should be able to query by indication area, target/mechanism, modality, phase, and company, and get back biologically related programs, not just literal matches.

**Recommended hybrid approach combining three complementary signals:**
1. **Structured / faceted filtering** over the normalized fields (phase, modality, company, target) — fast, precise, deterministic.
2. **Ontology-graph traversal** for adjacency — using the EFO/MONDO hierarchy to expand a query indication to parents, children, and siblings (this is the principled source of "biological adjacency").
3. **Semantic / vector search** over asset and indication descriptions for fuzzier therapeutic similarity that the ontology hierarchy doesn't capture.

These should be fusible (hybrid ranking) and, longer term, orchestratable by an **agentic search layer** that decomposes a natural-language investor query, applies the right mix of filters/traversal/semantic retrieval, and assembles results. Build the structured + ontology-graph + vector foundation first; layer the agentic orchestration on top once retrieval primitives are solid.

---

## 7. Change Tracking & Provenance (Phase 2 — schema-ready now)

This is deferred to Phase 2 for *features*, but the **data model and pipeline must be designed for it from day one** so it is additive, not a rewrite.

**Requirements to design for:**
- Detect and record phase changes, new program additions, discontinuations, and continuations across re-scrapes.
- Answer point-in-time questions: "what did Company X's pipeline look like on date Y," and "when did asset Z move from Phase 2 to Phase 3."

**Recommended foundations:**
- A **layered (medallion-style) data flow:** an immutable raw landing layer (every scrape snapshot stored with timestamp + content hash) → normalized layer → curated/published layer.
- **Bitemporal / effective-dated modeling** (valid time + transaction time), or an event-sourced change log, so history is reconstructable and corrections are distinguishable from real-world changes.
- **Snapshot diffing** to generate discrete, queryable change events from successive scrapes.

Designing the raw-snapshot + effective-dating layer in Phase 1 is the single most important forward-compatibility decision in this brief.

---

## 8. Delivery Surfaces (Phase 2)

- **Web UI:** investor-facing front end for search, filtering, and asset/company drill-down.
- **API:** programmatic access (REST and/or GraphQL) to the curated layer.
- **MCP server:** expose the database as tools so the data can be queried by LLM agents programmatically.

Phase 1 should expose a clean internal service/query layer that these three surfaces can all sit on, so they are presentation layers over a shared core rather than parallel implementations.

---

## 9. Non-Functional Requirements & Data Quality
- **Entity resolution / deduplication:** the same asset can appear on multiple companies' pages (partnered assets) under different names/codes; resolve to a single `asset_id` while preserving each company's view.
- **Provenance on every field:** source URL, fetch timestamp, raw artifact reference, and extraction method must be traceable for any value (investors will need to trust and audit the data).
- **Resilience & maintainability:** extraction should degrade gracefully and flag failures rather than silently producing wrong data; layout changes on a site should not silently corrupt records.
- **Good-citizen scraping:** respect robots.txt, throttle/rate-limit, identify the crawler, and cache rendered artifacts to avoid redundant fetches.
- **Quality gates:** validation, an "unmapped/needs-review" queue for low-confidence normalizations and ontology mappings, and coverage metrics per company.

---

## 10. Risks & Open Decisions (for the architecture phase)
- **Heterogeneous extraction (highest risk):** image/PDF-based pipeline charts will require vision-based extraction; plan for it explicitly.
- **Legal/ToS review:** scraping public disclosures is generally workable, but website terms, jurisdiction, and rate limits should be reviewed before scale-out, especially for a commercial investor product. *(Not a technical blocker; flagging for diligence.)*
- **Open decisions to resolve in design:**
  - Disease ontology of record — EFO vs. MONDO (or both, with crosswalk)?
  - Whether to adopt Open Targets as a primary enrichment backbone vs. composing OLS + ChEMBL + UniProt directly.
  - Re-scrape cadence (drives the change-tracking design and infra cost).
  - Build vs. buy for headless rendering / extraction orchestration.
  - Single-store vs. polyglot persistence (relational core + vector index + graph/ontology traversal).

---

## 11. Phasing Summary

| | Phase 1 (build now) | Phase 2 (design for now) |
|---|---|---|
| **Ingestion** | Generalized scrape + structured extraction, ~200 companies, raw-artifact preservation | Re-scrape cadence + change detection |
| **Schema** | ID-anchored company/asset/program, normalization, ontology mapping, **effective-dated/bitemporal foundation** | Full history queries, change events |
| **Search** | Structured + ontology-graph + semantic (hybrid) | Agentic orchestration layer |
| **Access** | Internal query/service layer | Web UI, API, MCP |
| **Scale** | Architected for 500+ companies | Operating at 500+ |

---

*This brief is intended as the upstream scope input for an architecture/coding engine. It deliberately fixes requirements and IDs the hard problems while leaving stack and detailed design choices open for that engine to resolve.*
