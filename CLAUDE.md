# CLAUDE.md — working in this repo

Pharma pipeline intelligence: an autonomous factory that scrapes each company's **own**
pipeline page, extracts the programs, QA-gates them, loads them to a Postgres medallion DB
(bronze → silver → gold), then enriches and publishes. Entry point: the `pipeline` Typer CLI
(`pipeline_intel/cli.py`). Read `README.md` for the flow; `docs/ENGINEERING_PLAN.md` for depth.

## Non-negotiable data principles

- **Disclosed always wins.** Company-scraped values are primary. Enrichment (Open Targets, OLS)
  may ONLY fill genuine gaps and is ALWAYS source-tagged (`disclosed` vs `open_targets`) — never
  overwrite or take credit for disclosed data. Modality is *never* derived; set it only when the
  company states it.
- **Verbatim correctness over coverage.** Better to leave a field unresolved than to publish a
  wrong value. Failures go to `needs_repair`, never to gold.
- **Real pipeline page only.** The source must be the company's own pipeline page — never news,
  FAQ, SEC filings, or press releases.
- **Don't scale before the gate.** Ingestion does not scale past the pilot until the eval gate
  passes (precision ≥0.95 / recall ≥0.90). `pipeline eval` refuses to score unlabeled drafts.

## Architecture (medallion)

- **bronze** = immutable `snapshot`s (raw HTML/PNG/PDF in object storage). **silver** =
  `extraction` rows (verbatim LLM output, re-derivable). **gold** = normalized entities + SCD2
  `program_version` (exactly one open row per program, `valid_to IS NULL`). Gold rebuilds from
  silver; every gold value traces back to a snapshot + extraction.
- Package is organized by stage: `ingest/ → extract/ → quality/ → gold/ →
  normalize/,ontology/,search/ → history/ → publish/`. Orchestration on top: `cli.py`,
  `batch.py`, `universe.py`, `onboard.py`.
- IDs are ULIDs. The external join key (to the trading / company DBs) is `company.ticker`.
- `publish/` is a *regenerable contract schema* (`published.*`) for the downstream trading DB —
  idempotent DDL via `pipeline publish`, NOT an Alembic migration. See `docs/PUBLISHED_DB.md`.

## Working in the repo

- Everything runs under `uv`: `uv run pipeline ...`, `uv run pytest -q`, `uv run ruff check .`.
- **DB setup order matters:** `uv run alembic upgrade head` THEN `uv run pipeline seed` (controlled
  vocab). Tests/CI fail against an unseeded DB (FK on `phase_vocab`).
- DB-backed tests use the `tx` fixture (a transaction that is ALWAYS rolled back — no gold
  pollution) and build synthetic data with no network. They skip if no Postgres is reachable.
- Lint is ruff (line-length 110); `migrations/versions` and `experiments/` are excluded.
- Commit/push only when asked; branch off `main` first.

## Notes

- Two agents work this repo (Claude Code + Codex via `.codex/`). This file is the shared source of
  truth — `AGENTS.md` symlinks to it. Keep guidance here, not duplicated.
- `docs/` holds point-in-time historical design docs (ENGINEERING_PLAN, the brief, the Wayback
  plan) — don't rewrite them to "current state".
