"""Hybrid search — the one entry point the quant front doors (package + REST) call.

Combines three signals, all fast and DETERMINISTIC (no LLM in the query path):
  1. structured  — exact filters on normalized fields (target HGNC, therapeutic_area, phase, …)
  2. lexical     — substring/verbatim match (catches "KRAS" inside "KRAS G12C inhibitor")
  3. vector      — pgvector cosine over embeddings (catches synonyms/paraphrases: K-Ras, anti-KRAS)

Free-text `query` ranks candidates by reciprocal-rank fusion of lexical + vector. Structured args
(`target`, `indication`, `phase`, …) filter for recall first. `as_of` gives point-in-time reads
for backtests (no lookahead). Same inputs → byte-identical output ordering.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import (
    Asset,
    AssetSynonym,
    AssetTarget,
    Indication,
    IndicationMapping,
    Program,
    ProgramVersion,
    Target,
)
from pipeline_intel.search.facets import _current_program_query, adjacent_curies

_RRF_K = 60          # reciprocal-rank-fusion damping
_CANDIDATE_CAP = 1500  # max rows pulled before ranking (data is small; keeps it bounded)


def search(
    s: Session,
    query: str | None = None,
    *,
    target: str | None = None,
    indication: str | None = None,
    phase: str | None = None,
    modality: str | None = None,
    therapeutic_area: str | None = None,
    company_id: str | None = None,
    status: str | None = None,
    active_only: bool = True,
    as_of=None,
    semantic: bool = True,
    limit: int = 50,
) -> dict:
    """Hybrid program search. Returns {query, count, results:[ProgramRow]}."""
    stmt = _current_program_query(as_of)
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
    if target:
        stmt = stmt.where(Asset.asset_id.in_(_assets_for_target(target)))
    if indication:
        stmt = stmt.where(_indication_clause(s, indication))

    rows = [dict(r._mapping) for r in s.execute(stmt.limit(_CANDIDATE_CAP))]
    by_id = {r["program_id"]: r for r in rows}

    if query:
        order = _fuse(s, query, list(by_id), semantic)
    else:
        # structured browse: most-advanced phase first, then name — deterministic.
        order = [r["program_id"] for r in sorted(
            rows, key=lambda r: (-(_PHASE_RANK.get(r["phase_code"], -1)), r["asset_name"] or "",
                                 r["program_id"]))]

    results = [by_id[pid] for pid in order[:limit] if pid in by_id]
    return {"query": query, "count": len(results), "results": results}


def find_by_target(s: Session, gene: str, **kw) -> dict:
    """All programs whose asset hits `gene` (HGNC symbol or disclosed verbatim, e.g. KRAS)."""
    return search(s, target=gene, **kw)


def companies_by_indication(s: Session, disease: str, **kw) -> dict:
    """Programs for `disease`, expanded to sub/super-types via the disease ontology
    (e.g. 'lung cancer' includes NSCLC / SCLC)."""
    return search(s, indication=disease, **kw)


# ---- filter helpers -----------------------------------------------------------------------

def _assets_for_target(gene: str):
    """Asset ids hitting a gene: normalized HGNC, disclosed target verbatim, MoA text, or synonym.
    Covers both enriched (hgnc_symbol) and un-enriched (verbatim 'KRAS G12C') rows."""
    like = f"%{gene}%"
    via_target = (
        select(AssetTarget.asset_id)
        .join(Target, Target.target_id == AssetTarget.target_id, isouter=True)
        .where(Target.hgnc_symbol.ilike(like) | AssetTarget.verbatim.ilike(like))
    )
    via_syn = select(AssetSynonym.asset_id).where(AssetSynonym.synonym.ilike(like))
    return select(Asset.asset_id).where(
        Asset.asset_id.in_(via_target)
        | Asset.asset_id.in_(via_syn)
        | Asset.mechanism_verbatim.ilike(like)
    )


def _indication_clause(s: Session, disease: str):
    """Programs whose indication matches `disease` — ontology-expanded when the disease resolves
    to a CURIE (descendants + near ancestors), else a verbatim label match."""
    from sqlalchemy import or_

    like = f"%{disease}%"
    label_match = Indication.preferred_label.ilike(like)
    curie = s.execute(sql(
        "select im.curie from indication_mapping im join indication i on i.indication_id=im.indication_id "
        "where im.curie is not null and lower(i.preferred_label)=lower(:d) limit 1"), {"d": disease}).scalar()
    if not curie:
        # try the ontology term table directly (disease name may not be an indication we hold)
        curie = s.execute(sql(
            "select curie from ontology_term where lower(label)=lower(:d) limit 1"), {"d": disease}).scalar()
    if curie:
        curies = list(adjacent_curies(s, curie).keys())
        mapped = select(IndicationMapping.indication_id).where(
            IndicationMapping.curie.in_(curies),
            IndicationMapping.status.in_(("auto", "reviewed")),
        )
        return or_(Program.indication_id.in_(mapped), label_match)
    return label_match


# ---- ranking ------------------------------------------------------------------------------

def _fuse(s: Session, query: str, ids: list[str], semantic: bool) -> list[str]:
    """Reciprocal-rank fusion of lexical + vector rankings over the candidate ids."""
    lexical = _lexical_rank(s, query, ids)
    vector = _vector_rank(s, query, ids) if semantic else []
    scores: dict[str, float] = {}
    for ranking in (lexical, vector):
        for rank, pid in enumerate(ranking):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank)
    # deterministic: fused score desc, then program_id asc for stable ties.
    return sorted(ids, key=lambda pid: (-scores.get(pid, 0.0), pid))


def _lexical_rank(s: Session, query: str, ids: list[str]) -> list[str]:
    """Trigram similarity of the query against each candidate's name/target/indication text."""
    if not ids:
        return []
    rows = s.execute(sql("""
        select p.program_id,
               greatest(
                 similarity(lower(a.preferred_name), lower(:q)),
                 similarity(lower(coalesce(a.mechanism_verbatim,'')), lower(:q)),
                 similarity(lower(i.preferred_label), lower(:q)),
                 coalesce((select max(similarity(lower(coalesce(t.hgnc_symbol, at.verbatim)), lower(:q)))
                           from asset_target at left join target t on t.target_id=at.target_id
                           where at.asset_id=a.asset_id), 0)
               ) as sim
        from program p join asset a on a.asset_id=p.asset_id
        join indication i on i.indication_id=p.indication_id
        where p.program_id = any(:ids)
        order by sim desc, p.program_id"""), {"q": query, "ids": ids}).all()
    return [pid for pid, sim in rows if sim and sim > 0.05]


def _vector_rank(s: Session, query: str, ids: list[str]) -> list[str]:
    """Cosine ANN over program_embedding for the candidate ids. Empty if embeddings not built."""
    if not ids:
        return []
    if not s.execute(sql("select 1 from program_embedding limit 1")).first():
        return []
    from pipeline_intel.search.embed import embed_query  # noqa: PLC0415 — defers heavy model load

    qv = str(embed_query(query))
    rows = s.execute(sql("""
        select program_id from program_embedding
        where program_id = any(:ids)
        order by embedding <=> (:qv)::vector, program_id
        limit 200"""), {"ids": ids, "qv": qv}).all()
    return [r[0] for r in rows]


# Phase ranking for default ordering (most-advanced first). Mirrors phase_vocab sort.
_PHASE_RANK = {
    "approved": 8, "filed": 7, "phase_3": 6, "phase_2_3": 5, "phase_2": 4,
    "phase_1_2": 3, "phase_1": 2, "preclinical": 1, "discontinued": 0,
}
