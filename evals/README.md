# evals/ — scrape-completion criteria

Trusted ground-truth checks used to judge whether a pipeline scrape is **complete**
for a given company. A check is anything we can assert against a finished scrape — most
commonly a **total candidate count**, but also a screenshot, a spreadsheet, or a PDF of
the company's own pipeline page that the count was read off of.

## Layout

```
evals/
  expected_counts.yaml     # the manifest: company -> checks (the source of truth here)
  <slug>/                  # per-company evidence: screenshots, PDFs, spreadsheets
    ...
```

`<slug>` matches the key in `expected_counts.yaml` and the `tests/golden/<slug>` fixture
folder where one exists (e.g. `gsk`, `pfizer`, `astrazeneca`, `merck`, `insmed`).

## How a count check is applied

A *trusted* count is a hard gate. The pipeline already has the machinery:
`pipeline_intel/quality/checker.py:deterministic_verdict` takes a `known_expected_count`
and returns `verdict="fail"` when the extracted count is far off (reconciling against both
asset- and program-level counts). The flow is:

1. Add/verify the count here, with evidence in `evals/<slug>/`.
2. Promote the verified count into `config/companies.seed.yaml` as
   `known_expected_count` on the company's source, so live scrapes are gated on it.

So this folder is the **human-curated, auditable** layer; `companies.seed.yaml` is the
**machine** layer the scraper reads.

## Relationship to other eval assets

- `tests/golden/<slug>/` — full asset-level ground truth (`expected.json`). GSK is the
  one `labeled: true` golden set. Heavier than a count; used to score extraction quality.
- `Eval_data/` — gitignored scratch (currently a pipeline spreadsheet).

## Adding a company

1. `mkdir evals/<slug>` and drop the evidence (screenshot / PDF / `.xlsx`).
2. Add an entry to `expected_counts.yaml` with the count, `unit`, `evidence` path, and a
   `note` on where the number came from.

### `unit`
- `candidates` — distinct entries as the **company** presents them (a row / card); the
  headline number a human reads off the page.
- `assets` — distinct molecules/products (one asset, many indications).
- `programs` — distinct asset × indication rows.
