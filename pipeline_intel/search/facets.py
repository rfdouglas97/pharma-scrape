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
    IndicationMapping,
    OntologyClosure,
    OntologyTerm,
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
            Program.indication_id,
            Indication.preferred_label.label("indication"),
            ProgramVersion.indication_verbatim,
            ProgramVersion.phase_code,
            PhaseVocab.label.label("phase_label"),
            ProgramVersion.phase_verbatim,
            ProgramVersion.status,
            IndicationMapping.curie.label("efo_curie"),
            IndicationMapping.label.label("efo_label"),
            IndicationMapping.therapeutic_area,
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
        .outerjoin(PhaseVocab, PhaseVocab.code == ProgramVersion.phase_code)
        .outerjoin(
            IndicationMapping,
            (IndicationMapping.indication_id == Program.indication_id)
            & IndicationMapping.status.in_(("auto", "reviewed")),
        )
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
    therapeutic_area: str | None = None,
    active_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Faceted program search. `q` matches asset name / indication / target (ILIKE).

    active_only excludes discontinued/removed programs — the default "active pipeline"
    view investors expect, and the count that reconciles with company-stated totals
    (e.g. GSK's 'Pipeline changes / Removed' items drop out)."""
    stmt = _current_program_query()
    if phase:
        stmt = stmt.where(ProgramVersion.phase_code == phase)
    if modality:
        stmt = stmt.where(Asset.modality_code == modality)
    if company_id:
        stmt = stmt.where(Program.company_id == company_id)
    if therapeutic_area:
        stmt = stmt.where(IndicationMapping.therapeutic_area == therapeutic_area)
    if status:
        stmt = stmt.where(ProgramVersion.status == status)
    elif active_only:
        stmt = stmt.where(ProgramVersion.status != "discontinued")
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
        select(
            Program.company_id,
            func.count().filter(ProgramVersion.status != "discontinued").label("n_programs"),
            func.count().filter(ProgramVersion.status == "discontinued").label("n_discontinued"),
        )
        .join(ProgramVersion, ProgramVersion.program_id == Program.program_id)
        .where(ProgramVersion.valid_to.is_(None))
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
            func.coalesce(prog_counts.c.n_discontinued, 0).label("n_discontinued"),
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
    progs = search_programs(s, company_id=company_id, limit=2000)["results"]
    by_phase: dict[str, list] = {}
    discontinued: list = []
    for p in progs:
        if p["status"] == "discontinued":
            discontinued.append(p)
        else:
            by_phase.setdefault(p["phase_code"] or "unmapped", []).append(p)
    return {
        "company_id": company.company_id,
        "name": company.name,
        "ticker": company.ticker,
        "country": company.country,
        "website": company.website,
        "n_programs": sum(len(v) for v in by_phase.values()),
        "n_discontinued": len(discontinued),
        "programs_by_phase": by_phase,
        "discontinued": discontinued,
    }


def get_asset(s: Session, asset_id: str) -> dict | None:
    asset = s.get(Asset, asset_id)
    if asset is None:
        return None
    synonyms = s.execute(
        select(AssetSynonym.synonym, AssetSynonym.synonym_type).where(AssetSynonym.asset_id == asset_id)
    ).all()
    targets = s.execute(
        select(Target.name, Target.hgnc_symbol, AssetTarget.verbatim, AssetTarget.action, AssetTarget.source)
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
        "modality_source": asset.modality_source,
        "chembl_id": asset.chembl_id,
        "extras": asset.extras,
        "synonyms": [{"synonym": x.synonym, "type": x.synonym_type} for x in synonyms],
        "targets": [
            {"symbol": x.hgnc_symbol, "name": x.name, "verbatim": x.verbatim,
             "action": x.action, "source": x.source}
            for x in targets
        ],
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
    tas = s.execute(
        select(IndicationMapping.therapeutic_area)
        .where(IndicationMapping.therapeutic_area.isnot(None))
        .distinct()
    ).scalars().all()
    return {
        "phases": [{"code": p.code, "label": p.label} for p in phases],
        "modalities": sorted(modalities),
        "therapeutic_areas": sorted(tas),
        "statuses": ["active", "discontinued", "paused", "unknown"],
    }


def coverage(s: Session) -> list[dict]:
    """Per-company coverage/freshness metrics for the ops view."""
    rows = list_companies(s)
    return rows


# --- Ontology-adjacency search ---------------------------------------------
def adjacent_curies(s: Session, curie: str, max_ancestor_hops: int = 2) -> dict[str, dict]:
    """Expand a disease CURIE to itself + descendants (any depth, more specific) +
    ancestors within N hops (more general), via the precomputed closure. Returns
    {curie: {relation, distance}} where relation ∈ exact|descendant|ancestor."""
    out: dict[str, dict] = {curie: {"relation": "exact", "distance": 0}}

    # descendants: closure rows with this curie as ancestor
    for c, depth in s.execute(
        select(OntologyClosure.descendant_curie, OntologyClosure.depth)
        .where(OntologyClosure.ancestor_curie == curie, OntologyClosure.depth > 0)
    ):
        out.setdefault(c, {"relation": "descendant", "distance": depth})

    # ancestors within N hops: closure rows with this curie as descendant
    for c, depth in s.execute(
        select(OntologyClosure.ancestor_curie, OntologyClosure.depth)
        .where(
            OntologyClosure.descendant_curie == curie,
            OntologyClosure.depth > 0,
            OntologyClosure.depth <= max_ancestor_hops,
        )
    ):
        if c not in out:
            out[c] = {"relation": "ancestor", "distance": depth}
    return out


def programs_by_indication(
    s: Session, curie: str, max_ancestor_hops: int = 2, active_only: bool = True, limit: int = 500
) -> dict:
    """Programs whose mapped indication is the given disease or an adjacent one (sub/super-
    type). Each row carries `relation` + `distance` so the UI can show why it matched.
    This is the biology-aware query the brief centers on."""
    adj = adjacent_curies(s, curie, max_ancestor_hops)
    if not adj:
        return {"query_curie": curie, "adjacent": {}, "results": []}

    # indications mapped to any adjacent curie
    ind_to_curie = dict(s.execute(
        select(IndicationMapping.indication_id, IndicationMapping.curie).where(
            IndicationMapping.curie.in_(list(adj)),
            IndicationMapping.status.in_(("auto", "reviewed")),
        )
    ).all())
    if not ind_to_curie:
        return {"query_curie": curie, "adjacent": adj, "results": []}

    stmt = _current_program_query().where(Program.indication_id.in_(list(ind_to_curie)))
    if active_only:
        stmt = stmt.where(ProgramVersion.status != "discontinued")
    rows = []
    for r in s.execute(stmt.limit(limit)):
        d = dict(r._mapping)
        meta = adj.get(ind_to_curie.get(d["indication_id"]))
        rows.append(d | (meta or {}))
    # rank: exact > descendant > ancestor, then by distance
    rel_rank = {"exact": 0, "descendant": 1, "ancestor": 2}
    rows.sort(key=lambda x: (rel_rank.get(x.get("relation"), 9), x.get("distance", 9)))
    label = s.get(OntologyTerm, curie)
    return {
        "query_curie": curie,
        "query_label": label.label if label else None,
        "adjacent_count": len(adj),
        "results": rows,
    }


def list_indications(s: Session, q: str | None = None, mapped_only: bool = True) -> list[dict]:
    """Indications with their EFO mapping — for picking a disease to run adjacency on."""
    stmt = (
        select(
            Indication.indication_id, Indication.preferred_label,
            IndicationMapping.curie, IndicationMapping.label.label("efo_label"),
            IndicationMapping.status,
        )
        .outerjoin(IndicationMapping, (IndicationMapping.indication_id == Indication.indication_id)
                   & IndicationMapping.status.in_(("auto", "reviewed")))
    )
    if q:
        stmt = stmt.where(Indication.preferred_label.ilike(f"%{q}%"))
    if mapped_only:
        stmt = stmt.where(IndicationMapping.curie.isnot(None))
    return [dict(r._mapping) for r in s.execute(stmt.order_by(Indication.preferred_label).limit(500))]
