"""Firecrawl integration (REST v2 via httpx — no SDK dependency).

Two fallbacks behind the existing primitives, so the factory reaches more of the long tail:
  * firecrawl_search  — find a company's site/pipeline page when the LLM resolver doesn't
                        know the domain (e.g. obscure micro-caps like Boundless Bio).
  * firecrawl_scrape  — render a JS/canvas page to clean markdown when Playwright's text
                        extraction comes back empty (e.g. Aligos's pipeline rendered 0 text).

Both no-op gracefully (return [] / None) when FIRECRAWL_API_KEY is unset or on any error, so
they are safe optional fallbacks — never a hard dependency.
"""

from __future__ import annotations

from pipeline_intel.config import settings

_BASE = "https://api.firecrawl.dev/v2"
_SEARCH_TIMEOUT = 60.0
_SCRAPE_TIMEOUT = 120.0


def _api_key() -> str | None:
    return settings().firecrawl_api_key


def firecrawl_search(query: str, limit: int = 5) -> list[dict]:
    """Web search. Returns [{url, title, description}, ...] (newest/best first), or [] if no
    key / on error."""
    key = _api_key()
    if not key:
        return []
    import httpx  # noqa: PLC0415 — defer import

    try:
        resp = httpx.post(
            f"{_BASE}/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": query[:500], "limit": limit, "sources": [{"type": "web"}]},
            timeout=_SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        return (resp.json().get("data") or {}).get("web") or []
    except Exception:  # noqa: BLE001 — optional fallback, never raise
        return []


def firecrawl_scrape(url: str, wait_ms: int = 4000) -> str | None:
    """Render a page (incl. JS) to markdown. Returns the markdown string, or None if no key /
    on error / empty."""
    key = _api_key()
    if not key:
        return None
    import httpx  # noqa: PLC0415 — defer import

    try:
        resp = httpx.post(
            f"{_BASE}/scrape",
            headers={"Authorization": f"Bearer {key}"},
            json={"url": url, "formats": [{"type": "markdown"}],
                  "waitFor": wait_ms, "onlyMainContent": True},
            timeout=_SCRAPE_TIMEOUT,
        )
        resp.raise_for_status()
        md = (resp.json().get("data") or {}).get("markdown")
        return md or None
    except Exception:  # noqa: BLE001 — optional fallback, never raise
        return None
