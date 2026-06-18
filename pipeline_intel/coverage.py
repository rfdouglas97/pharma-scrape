"""Factory coverage / observability — the at-a-glance operational state of a run on many
companies: how many are at each pipeline_status, what's loaded to gold, and a per-company
breakdown with the source format actually used. Surfaced via `pipeline coverage` + the API.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import Company, CompanySource, Extraction, Program, Snapshot

# Map pipeline_status to a coarse health bucket for the summary.
_FAILURE_STATUSES = {"failed", "needs_repair"}


def factory_status(s: Session) -> dict:
    """Operational snapshot of the whole registry."""
    by_status = {
        (st or "none"): n
        for st, n in s.execute(
            select(Company.pipeline_status, func.count()).group_by(Company.pipeline_status)
        ).all()
    }

    gold_companies, gold_programs, gold_assets = s.execute(
        select(
            func.count(func.distinct(Program.company_id)),
            func.count(func.distinct(Program.program_id)),
            func.count(func.distinct(Program.asset_id)),
        )
    ).one()

    # latest extraction input_mode per company (what format actually fed extraction)
    companies: list[dict] = []
    for c in s.execute(select(Company).order_by(Company.name)).scalars():
        n_programs = s.execute(
            select(func.count(Program.program_id)).where(Program.company_id == c.company_id)
        ).scalar_one()
        latest = s.execute(
            select(CompanySource.source_type, Snapshot.fetched_at, Extraction.usage)
            .join(Snapshot, Snapshot.source_id == CompanySource.source_id)
            .join(Extraction, Extraction.snapshot_id == Snapshot.snapshot_id, isouter=True)
            .where(CompanySource.company_id == c.company_id)
            .order_by(Snapshot.fetched_at.desc())
            .limit(1)
        ).first()
        companies.append({
            "company": c.name,
            "ticker": c.ticker,
            "status": c.pipeline_status,
            "programs": n_programs,
            "source_type": latest[0] if latest else None,
            "input_mode": (latest[2] or {}).get("input_mode") if latest and latest[2] else None,
        })

    return {
        "by_status": by_status,
        "failures": sum(n for st, n in by_status.items() if st in _FAILURE_STATUSES),
        "gold": {
            "companies": gold_companies or 0,
            "programs": gold_programs or 0,
            "assets": gold_assets or 0,
        },
        "companies": companies,
    }
