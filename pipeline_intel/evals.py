"""Promote the human-curated trusted counts in evals/expected_counts.yaml into the QA gate.

A trusted count is a HARD completeness gate: a scrape whose extracted count is far off a
trusted total fails QA (deterministic_verdict with known_expected_count). This loader copies
those counts onto the matching company sources so live scrapes are gated on them — without
it, an incomplete scrape (e.g. Pfizer extracting 10 of 96) silently passes.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import Company, CompanySource

DEFAULT_PATH = Path("evals/expected_counts.yaml")


def load_expected_counts(s: Session, path: Path = DEFAULT_PATH) -> dict:
    """Set known_expected_count on each company's active sources from the trusted counts in
    expected_counts.yaml. Returns a summary (updated sources + skipped companies)."""
    data = yaml.safe_load(path.read_text())
    updated: list[dict] = []
    skipped: list[dict] = []

    for slug, entry in (data.get("companies") or {}).items():
        name = entry.get("company")
        check = next(
            (c for c in entry.get("checks", [])
             if c.get("type") == "count" and c.get("trusted") and c.get("expected") is not None),
            None,
        )
        if check is None:
            skipped.append({"slug": slug, "reason": "no trusted count"})
            continue
        company = s.execute(select(Company).where(Company.name == name)).scalar_one_or_none()
        if company is None:
            skipped.append({"slug": slug, "reason": f"company {name!r} not in registry"})
            continue
        sources = s.execute(
            select(CompanySource).where(
                CompanySource.company_id == company.company_id, CompanySource.active.is_(True)
            )
        ).scalars().all()
        if not sources:
            skipped.append({"slug": slug, "reason": "no active source"})
            continue
        count = int(check["expected"])
        for src in sources:
            src.known_expected_count = count
        updated.append({"slug": slug, "company": name, "known_expected_count": count,
                        "sources": len(sources), "unit": check.get("unit")})

    return {"updated": updated, "skipped": skipped}
