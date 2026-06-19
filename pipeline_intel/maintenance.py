"""Data-quality maintenance: reset a company's loaded data so it can be re-onboarded clean.

Used when a company was sourced from the wrong page (e.g. a press release that pre-dated the
news-URL guardrail) and its gold programs are wrong. reset_company removes everything derived
from that bad source — sources, snapshots, extractions, programs, and the assets it originated —
in FK-safe (children-first) order, leaving the Company row so re-onboarding repopulates it from
the correct pipeline page.
"""

from __future__ import annotations

from sqlalchemy import select, text

from pipeline_intel.config import settings
from pipeline_intel.db import session
from pipeline_intel.gold.models import Company, CompanySource
from pipeline_intel.onboard import onboard_company


def reset_company(s, company_id: int) -> dict:
    """Delete all of a company's sources/snapshots/extractions/programs/originated-assets,
    children-first, within the caller's transaction. Returns counts removed."""
    src_ids = [r for (r,) in s.execute(
        select(CompanySource.source_id).where(CompanySource.company_id == company_id))]
    snap_ids = _ids(s, "select snapshot_id from snapshot where source_id = any(:v)", src_ids)
    ext_ids = _ids(s, "select extraction_id from extraction where snapshot_id = any(:v)", snap_ids)
    prog_ids = _ids(s, "select program_id from program where company_id = :v",
                    company_id, scalar=True)
    asset_ids = _ids(s, "select asset_id from asset where originator_company_id = :v",
                     company_id, scalar=True)

    counts = {"programs": len(prog_ids), "assets": len(asset_ids),
              "extractions": len(ext_ids), "snapshots": len(snap_ids), "sources": len(src_ids)}

    # children-first (see FK graph): events/embeddings/versions/edges -> programs -> qa/extractions
    # -> snapshots -> assets -> sources.
    _del(s, "delete from change_event where company_id = :c or asset_id = any(:a) "
            "or from_snapshot_id = any(:s) or to_snapshot_id = any(:s)",
         c=company_id, a=asset_ids, s=snap_ids)
    _del(s, "delete from program_embedding where program_id = any(:p)", p=prog_ids)
    _del(s, "delete from program_version where program_id = any(:p)", p=prog_ids)
    _del(s, "delete from partnership where program_id = any(:p) or asset_id = any(:a) "
            "or source_extraction_id = any(:e)", p=prog_ids, a=asset_ids, e=ext_ids)
    _del(s, "delete from asset_target where asset_id = any(:a) or source_extraction_id = any(:e)",
         a=asset_ids, e=ext_ids)
    _del(s, "delete from asset_synonym where asset_id = any(:a) or source_extraction_id = any(:e)",
         a=asset_ids, e=ext_ids)
    _del(s, "delete from asset_alias where asset_id = any(:a)", a=asset_ids)
    _del(s, "delete from program where company_id = :c", c=company_id)
    _del(s, "delete from qa_report where extraction_id = any(:e)", e=ext_ids)
    _del(s, "delete from extraction where snapshot_id = any(:s)", s=snap_ids)
    _del(s, "delete from snapshot where source_id = any(:v)", v=src_ids)
    _del(s, "delete from asset where asset_id = any(:a)", a=asset_ids)
    _del(s, "delete from company_source where company_id = :c", c=company_id)

    s.get(Company, company_id).pipeline_status = "reset"
    return counts


def _ids(sess, sql: str, val, scalar: bool = False) -> list[int]:
    if not scalar and not val:
        return []
    rows = sess.execute(text(sql), {"v": val}).all()
    return [r[0] for r in rows]


def _del(sess, sql: str, **params) -> None:
    sess.execute(text(sql), params)


def reset_and_reonboard(tickers: list[str], publish_mode: str = "gated") -> list[dict]:
    """Reset each ticker's bad data (committed per company) then re-onboard from the corrected
    resolver. Returns one result row per ticker."""
    api_base = settings().company_db_url
    out: list[dict] = []
    for tk in tickers:
        with session() as s:
            row = s.execute(select(Company.company_id, Company.name).where(Company.ticker == tk)).first()
            if not row:
                out.append({"ticker": tk, "error": "not found"})
                continue
            cid, name = row
            removed = reset_company(s, cid)
            s.commit()
        res = onboard_company(name, tk, market_cap=_market_cap(api_base, tk),
                              run=True, publish_mode=publish_mode)
        out.append({
            "ticker": tk, "removed": removed,
            "new_url": res.get("pipeline_url"), "method": res.get("resolve_method"),
            "status": res.get("status"),
            "programs": (res.get("run") or {}).get("programs_extracted"),
            "loaded": (res.get("run") or {}).get("loaded"),
        })
    return out


def _market_cap(api_base: str, ticker: str) -> float | None:
    import httpx  # noqa: PLC0415

    try:
        r = httpx.get(f"{api_base}/companies", params={"limit": 5000}, timeout=30)
        r.raise_for_status()
        for c in r.json().get("results", []):
            if c.get("ticker") == ticker:
                return c.get("market_cap")
    except httpx.HTTPError:
        pass
    return None
