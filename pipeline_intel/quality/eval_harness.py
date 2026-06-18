"""Golden-set evaluation: run the live extractor against hand-labeled pipeline pages and
score it. This is the M1 gate — the dataset does not scale past the pilot set until the
extractor clears the thresholds below on a format-diverse golden set.

Fixture layout (one directory per labeled page under tests/golden/):
    tests/golden/<slug>/
        meta.json       {"company": "...", "url": "...", "format": "html_table|js|pdf|image"}
        page.txt        the rendered visible text (from a real snapshot)
        page.png        (optional) full-page screenshot
        expected.json   hand-labeled ExtractionResult (the gold standard)

`run_eval` calls the real model, so it needs ANTHROPIC_API_KEY. The scorer itself is
pure and unit-tested without the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pipeline_intel.extract.extractor import run_extraction
from pipeline_intel.extract.schemas import ExtractionResult
from pipeline_intel.quality.scorer import ScoreReport, score

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "tests" / "golden"

# The M1 gate (from ENGINEERING_PLAN): field-level precision >= 0.95, recall >= 0.90.
GATE_PRECISION = 0.95
GATE_RECALL = 0.90


@dataclass
class Fixture:
    slug: str
    company: str
    url: str
    fmt: str
    page_text: str
    screenshots: list[bytes]
    gold: ExtractionResult
    auto_reconciled: bool = False  # golden built from a structured file -> score covered dims only


def load_fixtures(golden_dir: Path = GOLDEN_DIR) -> list[Fixture]:
    fixtures: list[Fixture] = []
    if not golden_dir.exists():
        return fixtures
    for d in sorted(p for p in golden_dir.iterdir() if p.is_dir()):
        meta_path, expected_path = d / "meta.json", d / "expected.json"
        if not (meta_path.exists() and expected_path.exists()):
            continue
        meta = json.loads(meta_path.read_text())
        # Only human-verified fixtures count as ground truth. A model-seeded draft
        # (labeled != true) must never be scored against — that would grade the model
        # against its own output. Flip meta.labeled to true once you've corrected it.
        if meta.get("labeled") is not True:
            continue
        gold = ExtractionResult.model_validate_json(expected_path.read_text())
        page_text = (d / "page.txt").read_text() if (d / "page.txt").exists() else ""
        screenshots = [(d / "page.png").read_bytes()] if (d / "page.png").exists() else []
        fixtures.append(
            Fixture(
                slug=d.name,
                company=meta.get("company", d.name),
                url=meta.get("url", ""),
                fmt=meta.get("format", "unknown"),
                page_text=page_text,
                screenshots=screenshots,
                gold=gold,
                auto_reconciled=bool(meta.get("auto_reconciled")),
            )
        )
    return fixtures


@dataclass
class FixtureResult:
    slug: str
    fmt: str
    report: ScoreReport
    passed: bool


def run_eval(model: str | None = None, golden_dir: Path = GOLDEN_DIR) -> dict:
    """Run live extraction over every fixture, score it, and apply the gate."""
    from pipeline_intel.extract.client import DEFAULT_EXTRACTION_MODEL

    model = model or DEFAULT_EXTRACTION_MODEL
    fixtures = load_fixtures(golden_dir)
    results: list[FixtureResult] = []
    agg_tp = agg_fp = agg_fn = 0

    for fx in fixtures:
        predicted, _usage, _stop = run_extraction(
            fx.company, fx.url, fx.page_text, fx.screenshots, model
        )
        report = score(predicted, fx.gold, scope_to_gold_categories=fx.auto_reconciled)
        passed = report.overall.precision >= GATE_PRECISION and report.overall.recall >= GATE_RECALL
        results.append(FixtureResult(fx.slug, fx.fmt, report, passed))
        agg_tp += report.overall.tp
        agg_fp += report.overall.fp
        agg_fn += report.overall.fn

    micro_p = agg_tp / (agg_tp + agg_fp) if (agg_tp + agg_fp) else 1.0
    micro_r = agg_tp / (agg_tp + agg_fn) if (agg_tp + agg_fn) else 1.0
    gate_pass = bool(fixtures) and micro_p >= GATE_PRECISION and micro_r >= GATE_RECALL

    return {
        "model": model,
        "n_fixtures": len(fixtures),
        "micro_precision": round(micro_p, 4),
        "micro_recall": round(micro_r, 4),
        "gate_precision": GATE_PRECISION,
        "gate_recall": GATE_RECALL,
        "gate_pass": gate_pass,
        "fixtures": [
            {"slug": r.slug, "format": r.fmt, "passed": r.passed, **r.report.as_dict()}
            for r in results
        ],
    }
