"""Firecrawl integration (REST v2 via httpx — no SDK dependency).

Fallbacks behind the existing primitives so the factory reaches more of the long tail:
  * firecrawl_search  — find a company's site/pipeline page (resolver primary).
  * firecrawl_map     — list a site's URLs, keyword-filtered (resolver crawl fallback).
  * firecrawl_scrape  — render a JS/canvas page to markdown when Playwright gets ~nothing.

search/map RAISE FirecrawlError on a persistent failure (rate limit / network) so a transient
error is NOT mistaken for "no pipeline found" — the caller can retry instead of recording a
terminal `unresolved`. scrape is a soft fallback and returns None on error. All no-op (empty /
None) when FIRECRAWL_API_KEY is unset.
"""

from __future__ import annotations

import time

from pipeline_intel.config import settings

_BASE = "https://api.firecrawl.dev/v2"
_SEARCH_TIMEOUT = 60.0
_SCRAPE_TIMEOUT = 120.0
_MAX_ATTEMPTS = 4  # retry 429 / 5xx / network with exponential backoff (2,4,8s)


class FirecrawlError(RuntimeError):
    pass


def _api_key() -> str | None:
    return settings().firecrawl_api_key


def _post(path: str, body: dict, timeout: float) -> dict | None:
    """POST with retry/backoff on rate-limit / 5xx / network. Returns the JSON body, None if
    no API key, or raises FirecrawlError after exhausting retries."""
    key = _api_key()
    if not key:
        return None
    import httpx  # noqa: PLC0415 — defer import

    last = "unknown"
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = httpx.post(f"{_BASE}/{path}", headers={"Authorization": f"Bearer {key}"},
                              json=body, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"http {resp.status_code}"
            else:
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            last = str(exc)[:160]
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(2 * (2 ** attempt))
    raise FirecrawlError(f"firecrawl {path} failed after {_MAX_ATTEMPTS} attempts: {last}")


def firecrawl_search(query: str, limit: int = 5) -> list[dict]:
    """Web search -> [{url, title, description}, ...]. [] if no key. Raises on persistent error."""
    data = _post("search", {"query": query[:500], "limit": limit, "sources": [{"type": "web"}]},
                 _SEARCH_TIMEOUT)
    if data is None:
        return []
    return (data.get("data") or {}).get("web") or []


def firecrawl_map(url: str, search: str | None = None, limit: int = 60) -> list[dict]:
    """List a site's URLs -> [{url, title}, ...]. [] if no key. Raises on persistent error."""
    body: dict = {"url": url, "limit": limit}
    if search:
        body["search"] = search
    data = _post("map", body, _SEARCH_TIMEOUT)
    if data is None:
        return []
    return data.get("links") or []


def firecrawl_scrape(url: str, wait_ms: int = 4000) -> str | None:
    """Render a page (incl. JS) to markdown. Soft fallback: returns None on no-key / any error."""
    try:
        data = _post("scrape", {"url": url, "formats": [{"type": "markdown"}],
                                "waitFor": wait_ms, "onlyMainContent": True}, _SCRAPE_TIMEOUT)
    except FirecrawlError:
        return None
    if data is None:
        return None
    return (data.get("data") or {}).get("markdown") or None
