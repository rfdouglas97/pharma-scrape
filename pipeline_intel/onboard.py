"""One-shot autonomous onboarding: name + ticker -> resolve a validated pipeline source ->
register the company + source -> run the gated factory. The unit of scaling to hundreds.

Every attempt is recorded in the company registry (including `unresolved` ones), so the
registry doubles as the ledger of what we've processed — see `universe.py` for the walk.
"""

from __future__ import annotations

from sqlalchemy import or_, select

from pipeline_intel.company_resolver import resolve_company_source
from pipeline_intel.db import session
from pipeline_intel.gold.models import Company, CompanySource


def _get_or_create_company(s, name: str, ticker: str | None, market_cap: float | None) -> Company:
    company = s.execute(
        select(Company).where(
            or_(Company.name == name, *([Company.ticker == ticker] if ticker else []))
        ).limit(1)
    ).scalar_one_or_none()
    if company is None:
        company = Company(name=name, ticker=ticker, status="active",
                          pipeline_status="unverified_source")
        s.add(company)
        s.flush()
    if ticker and not company.ticker:
        company.ticker = ticker
    if market_cap is not None:
        company.market_cap_usd = market_cap
    return company


def onboard_company(
    name: str, ticker: str | None = None, market_cap: float | None = None,
    run: bool = True, publish_mode: str = "gated", resolve_fn=None,
) -> dict:
    """Resolve -> register (always, as the ledger) -> scrape. Returns a summary; never raises."""
    try:
        resolved = (resolve_fn or (lambda: resolve_company_source(name, ticker)))()
    except Exception as exc:  # noqa: BLE001 — one company must never crash a batch
        resolved = {"pipeline_url": None, "method": None, "validated": False,
                    "rationale": f"resolve_error: {str(exc)[:160]}"}

    out = {
        "company": name, "ticker": ticker,
        "pipeline_url": resolved.get("pipeline_url"),
        "resolve_method": resolved.get("method"),
        "validated": resolved.get("validated"),
    }

    with session() as s:
        company = _get_or_create_company(s, name, ticker, market_cap)
        company_name = company.name
        if not resolved.get("pipeline_url"):
            # Record the attempt so the universe walk doesn't retry it endlessly.
            company.pipeline_status = "unresolved"
            out["status"] = "unresolved"
            out["rationale"] = resolved.get("rationale")
            return out
        existing = s.execute(
            select(CompanySource).where(CompanySource.company_id == company.company_id,
                                        CompanySource.url == resolved["pipeline_url"])
        ).scalar_one_or_none()
        if existing is None:
            s.add(CompanySource(company_id=company.company_id, url=resolved["pipeline_url"],
                                active=True, render_config={}))
            out["registered_source"] = True

    if not run:
        out["status"] = "registered" + ("" if resolved.get("validated") else " (unvalidated url)")
        return out

    # Run the full gated factory. Its QA + completeness gates are the safety net for an
    # unvalidated URL, so we proceed and let bad data quarantine itself to needs_repair.
    from pipeline_intel.batch import run_company_pipeline

    r = run_company_pipeline(company_name, publish_mode=publish_mode).as_dict()
    out["run"] = {
        "status": r.get("status"),
        "programs_extracted": sum(e.get("n_programs", 0) for e in r.get("extractions", [])),
        "qa": [q.get("verdict") for q in r.get("qa", [])],
        "loaded": len(r.get("loaded", [])),
        "error": r.get("error"),
    }
    out["status"] = r.get("status")
    return out
