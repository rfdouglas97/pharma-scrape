"""One-shot autonomous onboarding: name + ticker -> resolve a validated pipeline source ->
register the company + source -> run the gated factory. This is the capstone that turns the
discovery/extraction primitives into "feed a ticker, get a scraped pipeline" — the unit of
scaling to hundreds of companies.
"""

from __future__ import annotations

from sqlalchemy import or_, select

from pipeline_intel.company_resolver import resolve_company_source
from pipeline_intel.db import session
from pipeline_intel.extract.client import SONNET_MODEL
from pipeline_intel.gold.models import Company, CompanySource


def onboard_company(
    name: str, ticker: str | None = None, model: str = SONNET_MODEL,
    run: bool = True, publish_mode: str = "gated", resolve_fn=None,
) -> dict:
    """Resolve -> register -> scrape. Returns a summary; never raises (failures are reported)."""
    resolved = (resolve_fn or (lambda: resolve_company_source(name, ticker, model)))()
    out = {
        "company": name, "ticker": ticker,
        "pipeline_url": resolved.get("pipeline_url"),
        "resolve_method": resolved.get("method"),
        "validated": resolved.get("validated"),
    }
    if not resolved.get("pipeline_url"):
        out["status"] = "unresolved"  # no pipeline URL found -> human curation needed
        out["rationale"] = resolved.get("rationale")
        return out

    # Register the company + source (idempotent).
    with session() as s:
        company = s.execute(
            select(Company).where(
                or_(Company.name == name,
                    *( [Company.ticker == ticker] if ticker else [] ))
            ).limit(1)
        ).scalar_one_or_none()
        if company is None:
            company = Company(name=name, ticker=ticker, status="active",
                              pipeline_status="unverified_source")
            s.add(company)
            s.flush()
            out["registered_company"] = True
        company_name = company.name
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
