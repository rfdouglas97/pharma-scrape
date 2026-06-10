"""Shared query layer over gold. Every delivery surface (API, UI, future MCP) calls
these functions — they are the single place that knows how to read the dataset.

Phase-1 scope: structured/faceted filtering + drill-down with provenance on every row.
Ontology-adjacency and vector search (M6) will be added here as additional primitives,
behind the same interface.
"""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import (
    Asset,
    AssetSynonym,
    AssetTarget,
    Company,
    CompanySource,
    Indication,
    Partnership,
    PhaseVocab,
    Program,
    ProgramVersion,
    Snapshot,
    Target,
)


def _current_program_query() -> Select:
    """Base join: one row per program at its CURRENT version (valid_to IS NULL), with
    asset, indication, company, and provenance (last-seen snapshot)."""
    return (
        select(
            Program.program_id,
            Asset.asset_id,
            Asset.preferred_name.label("asset_name"),
            Asset.modality_code,
            Asset.modality_verbatim,
            Indication.preferred_label.label("indication"),
            ProgramVersion.indication_verbatim,
            ProgramVersion.phase_code,
            ProgramVersion.phase_verbatim,
            ProgramVersion.status,
            Company.company_id,
            Company.name.label("company_name"),
            Company.ticker,
            CompanySource.url.label("source_url"),
            Snapshot.fetched_at,
            ProgramVersion.last_seen_snapshot_id.label("snapshot_id"),
        )
        .join(ProgramVersion, ProgramVersion.program_id == Program.program_id)
        .join(Asset, Asset.asset_id == Program.asset_id)
        .join(Indication, Indication.indication_id == Program.indication_id)
        .join(Company, Company.company_id == Program.company_id)
        .outerjoin(Snapshot, Snapshot.snapshot_id == ProgramVersion.last_seen_snapshot_id)
        .outerjoin(CompanySource, CompanySource.source_id == Snapshot.source_id)
        .where(ProgramVersion.valid_to.is_(None))
    )


def search_programs(
    s: Session,
    q: str | None = None,
    phase: str | None = None,
    modality: str | None = None,
    company_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Faceted program search. `q` matches asset name / indication / target (ILIKE)."""
    stmt = _current_program_query()
    if phase:
        stmt = stmt.where(ProgramVersion.phase_code == phase)
    if modality:
        stmt = stmt.where(Asset.modality_code == modality)
    if company_id:
        stmt = stmt.where(Program.company_id == company_id)
    if status:
        stmt = stmt.where(ProgramVersion.status == status)
    if q:
        like = f"%{q}%"
        target_assets = select(AssetTarget.asset_id).join(
            Target, Target.target_id == AssetTarget.target_id
        ).where(or_(Target.name.ilike(like), AssetTarget.verbatim.ilike(like)))
        syn_assets = select(AssetSynonym.asset_id).where(AssetSynonym.synonym.ilike(like))
        stmt = stmt.where(
            or_(
                Asset.preferred_name.ilike(like),
                Indication.preferred_label.ilike(like),
                Asset.asset_id.in_(target_assets),
                Asset.asset_id.in_(syn_assets),
            )
        )

    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = s.execute(count_stmt).scalar_one()

    phase_order = (
        select(PhaseVocab.code, PhaseVocab.sort_order).subquery()
    )
    stmt = (
        stmt.outerjoin(phase_order, phase_order.c.code == ProgramVersion.phase_code)
        .order_by(phase_order.c.sort_order.desc().nullslast(), Asset.preferred_name)
        .limit(limit)
        .offset(offset)
    )
    rows = [dict(r._mapping) for r in s.execute(stmt)]
    return {"total": total, "limit": limit, "offset": offset, "results": rows}


def list_companies(s: Session) -> list[dict]:
    """Companies with program counts + freshness (companies with no programs still listed)."""
    prog_counts = (
        select(Program.company_id, func.count().label("n_programs"))
        .group_by(Program.company_id)
        .subquery()
    )
    last_fetch = (
        select(CompanySource.company_id, func.max(Snapshot.fetched_at).label("last_fetched"))
        .join(Snapshot, Snapshot.source_id == CompanySource.source_id)
        .group_by(CompanySource.company_id)
        .subquery()
    )
    stmt = (
        select(
            Company.company_id, Company.name, Company.ticker, Company.country,
            func.coalesce(prog_counts.c.n_programs, 0).label("n_programs"),
            last_fetch.c.last_fetched,
        )
        .outerjoin(prog_counts, prog_counts.c.company_id == Company.company_id)
        .outerjoin(last_fetch, last_fetch.c.company_id == Company.company_id)
        .order_by(func.coalesce(prog_counts.c.n_programs, 0).desc(), Company.name)
    )
    return [dict(r._mapping) for r in s.execute(stmt)]


def get_company(s: Session, company_id: str) -> dict | None:
    company = s.get(Company, company_id)
    if company is None:
        return None
    progs = search_programs(s, company_id=company_id, limit=1000)["results"]
    by_phase: dict[str, list] = {}
    for p in progs:
        by_phase.setdefault(p["phase_code"] or "unmapped", []).append(p)
    return {
        "company_id": company.company_id,
        "name": company.name,
        "ticker": company.ticker,
        "country": company.country,
        "website": company.website,
        "n_programs": len(progs),
        "programs_by_phase": by_phase,
    }


def get_asset(s: Session, asset_id: str) -> dict | None:
    asset = s.get(Asset, asset_id)
    if asset is None:
        return None
    synonyms = s.execute(
        select(AssetSynonym.synonym, AssetSynonym.synonym_type).where(AssetSynonym.asset_id == asset_id)
    ).all()
    targets = s.execute(
        select(Target.name, AssetTarget.verbatim, AssetTarget.action)
        .join(AssetTarget, AssetTarget.target_id == Target.target_id)
        .where(AssetTarget.asset_id == asset_id)
    ).all()
    partners = s.execute(
        select(Partnership.partner_name_verbatim, Partnership.role, Partnership.territory)
        .where(Partnership.asset_id == asset_id)
    ).all()
    programs = s.execute(
        _current_program_query().where(Program.asset_id == asset_id)
    )
    return {
        "asset_id": asset.asset_id,
        "preferred_name": asset.preferred_name,
        "modality_code": asset.modality_code,
        "modality_verbatim": asset.modality_verbatim,
        "extras": asset.extras,
        "synonyms": [{"synonym": x.synonym, "type": x.synonym_type} for x in synonyms],
        "targets": [{"name": x.name, "verbatim": x.verbatim, "action": x.action} for x in targets],
        "partners": [
            {"name": x.partner_name_verbatim, "role": x.role, "territory": x.territory}
            for x in partners
        ],
        "programs": [dict(r._mapping) for r in programs],
    }


def facet_values(s: Session) -> dict:
    """Distinct facet values present in current data, for populating filter UIs."""
    phases = s.execute(
        select(PhaseVocab.code, PhaseVocab.label, PhaseVocab.sort_order)
        .where(PhaseVocab.code.in_(
            select(ProgramVersion.phase_code).where(ProgramVersion.valid_to.is_(None)).distinct()
        ))
        .order_by(PhaseVocab.sort_order)
    ).all()
    modalities = s.execute(
        select(Asset.modality_code).where(Asset.modality_code.isnot(None)).distinct()
    ).scalars().all()
    return {
        "phases": [{"code": p.code, "label": p.label} for p in phases],
        "modalities": sorted(modalities),
        "statuses": ["active", "discontinued", "paused", "unknown"],
    }


def coverage(s: Session) -> list[dict]:
    """Per-company coverage/freshness metrics for the ops view."""
    rows = list_companies(s)
    return rows
