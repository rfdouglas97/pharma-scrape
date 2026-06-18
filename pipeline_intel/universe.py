"""Systematic universe walk: onboard companies from the external company DB in ascending
market-cap order, tracking progress via our own registry (every onboarded company — including
`unresolved` ones — is recorded, so we never re-attempt and can resume where we left off).
"""

from __future__ import annotations

from sqlalchemy import select

from pipeline_intel.config import settings
from pipeline_intel.db import session
from pipeline_intel.gold.models import Company
from pipeline_intel.onboard import onboard_company


def fetch_universe(api_base: str, min_market_cap: float = 0.0, limit: int = 5000) -> list[dict]:
    """Companies from the DB API with a market cap, ascending by market cap."""
    import httpx  # noqa: PLC0415 — defer import

    resp = httpx.get(f"{api_base}/companies",
                     params={"min_market_cap": min_market_cap, "limit": limit}, timeout=30)
    resp.raise_for_status()
    rows = resp.json().get("results", [])
    return sorted((c for c in rows if c.get("market_cap")), key=lambda c: c["market_cap"])


def attempted_tickers(s) -> set[str]:
    """Tickers already in our registry (= already attempted)."""
    return {t for (t,) in s.execute(select(Company.ticker).where(Company.ticker.isnot(None)))}


def onboard_universe(
    api_base: str | None = None, min_market_cap: float = 30_000_000, limit: int = 10,
    run: bool = True, publish_mode: str = "gated",
) -> dict:
    """Onboard the next `limit` un-attempted companies (ascending market cap, >= floor)."""
    api_base = api_base or settings().company_db_url
    universe = fetch_universe(api_base, min_market_cap)
    universe_tickers = {c.get("ticker") for c in universe}

    with session() as s:
        already = attempted_tickers(s)
    batch = [c for c in universe if c.get("ticker") not in already][:limit]

    results: list[dict] = []
    for c in batch:
        out = onboard_company(c["name"], c.get("ticker"), market_cap=c.get("market_cap"),
                              run=run, publish_mode=publish_mode)
        results.append({
            "ticker": c.get("ticker"),
            "market_cap_m": round(c["market_cap"] / 1e6, 1),
            "status": out.get("status"),
            "pipeline_url": out.get("pipeline_url"),
            "method": out.get("resolve_method"),
            "programs": (out.get("run") or {}).get("programs_extracted"),
            "loaded": (out.get("run") or {}).get("loaded"),
        })

    with session() as s:
        now_attempted = attempted_tickers(s) & universe_tickers
    return {
        "floor_m": round(min_market_cap / 1e6, 1),
        "universe_total": len(universe),
        "attempted_total": len(now_attempted),
        "remaining": len(universe) - len(now_attempted),
        "batch": results,
    }
