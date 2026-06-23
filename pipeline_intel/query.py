"""Public query API for external consumers (e.g. a quant trading system).

Import this and call it — it manages the DB session and returns plain dicts (or a pandas
DataFrame). Every call is fast and DETERMINISTIC (no LLM), so results are reproducible across
runs and safe for backtests. For point-in-time reads pass `as_of=<datetime>`.

    from pipeline_intel.query import find_by_target, companies_by_indication, search
    df = find_by_target("KRAS", as_dataframe=True)
    df = companies_by_indication("lung cancer", as_dataframe=True)
    df = search("KRAS G12C inhibitor", phase="phase_3", as_dataframe=True)   # free-text hybrid
"""

from __future__ import annotations

from pipeline_intel.db import session
from pipeline_intel.search import hybrid


def _out(res: dict, as_dataframe: bool):
    if not as_dataframe:
        return res
    import pandas as pd  # noqa: PLC0415 — optional dep, only for DataFrame callers

    return pd.DataFrame(res["results"])


def search(query: str | None = None, *, as_dataframe: bool = False, **kw):
    """Hybrid search. kw: target, indication, phase, modality, therapeutic_area, company_id,
    status, active_only, as_of, semantic, limit. Returns a dict (or DataFrame of the rows)."""
    with session() as s:
        res = hybrid.search(s, query, **kw)
    return _out(res, as_dataframe)


def find_by_target(gene: str, *, as_dataframe: bool = False, **kw):
    """All programs hitting `gene` (HGNC symbol or disclosed verbatim, e.g. 'KRAS')."""
    with session() as s:
        res = hybrid.find_by_target(s, gene, **kw)
    return _out(res, as_dataframe)


def companies_by_indication(disease: str, *, as_dataframe: bool = False, **kw):
    """Programs for `disease`, ontology-expanded to sub/super-types (lung cancer -> NSCLC/SCLC)."""
    with session() as s:
        res = hybrid.companies_by_indication(s, disease, **kw)
    return _out(res, as_dataframe)
