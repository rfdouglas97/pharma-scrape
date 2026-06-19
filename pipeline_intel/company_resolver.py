"""Resolve a company's pipeline source from just its name + ticker — the front of fully
autonomous discovery, so a list of tickers can be scraped hands-off.

No URL guessing, no LLM — pull the pipeline page programmatically via Firecrawl, then check it:
  (i)  search "<company> pipeline" and content-check the company's own-domain results;
  (ii) if none check out, map the company site for "pipeline" and content-check those.

Validation (validate_pipeline_page) is content-based — a real pipeline page carries phase
vocabulary or a pipeline image on a real page — so traps like "/pipeline/" 301-redirecting to
a stray .jpg are rejected and discovery moves on to the page that actually has the pipeline.
"""

from __future__ import annotations

import re

from pipeline_intel.firecrawl_client import FirecrawlError, firecrawl_map, firecrawl_search
from pipeline_intel.ingest.classify import classify_rendered_page, phase_hits

# Rank a site's links by how pipeline-like the URL path + title are (callers pre-filter to the
# company's own domain, so no domain check here). Many pipelines live under /science, /programs,
# /portfolio, not literally /pipeline.
_PATH_SECTION_HINTS = ("science", "research", "development", "portfolio", "products",
                       "medicines", "candidates", "innovation", "programs")


def _score_pipeline_link(url: str, title: str) -> int:
    from urllib.parse import urlparse

    path = urlparse(url).path.lower()
    t = (title or "").lower()
    score = 0
    if "pipeline" in path:
        score += 100
    if t == "pipeline":
        score += 90
    elif "pipeline" in t:
        score += 70
    if any(h in path for h in _PATH_SECTION_HINTS):
        score += 20
    return score


def _name_token(name: str) -> str:
    # first significant word of the company name, e.g. "Bristol Myers Squibb" -> "bristol"
    cleaned = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    words = [w for w in cleaned.split() if len(w) > 2 and w not in ("the", "inc", "ltd", "plc")]
    return words[0] if words else name.lower()


def validate_pipeline_page(url: str, company_name: str, render_fn) -> dict:
    """Render a candidate URL and judge whether it's a real pipeline page. ok=True when it
    shows pipeline structure / phase text / a pipeline image."""
    try:
        r = render_fn(url)
    except Exception as exc:  # noqa: BLE001 — a failed render just means not validated
        return {"ok": False, "reason": f"render failed: {str(exc)[:120]}"}
    if r.http_status and r.http_status >= 400:
        return {"ok": False, "reason": f"http {r.http_status}"}
    kind = classify_rendered_page(r.html, r.text, r.meta.get("pipeline_image_urls"))
    hits = phase_hits(r.text)
    has_image = bool(r.meta.get("pipeline_image_urls"))
    body_len = len((r.text or "").strip())
    # CONTENT-based validation: a real pipeline page either carries phase vocabulary in its
    # text, or is a genuine image-backed page (a pipeline image on a real HTML page with body
    # text). This rejects the common trap where "/pipeline/" 301-redirects to a bare .jpg —
    # the browser renders the image with ~no surrounding text, so it fails both conditions and
    # the resolver falls through to search, which finds the real page (e.g. /science/...).
    pipeline_signal = hits >= 3 or (has_image and body_len >= 200)
    return {
        "ok": bool(pipeline_signal),
        "source_type": kind,
        "phase_hits": hits,
        "has_pipeline_image": has_image,
        "body_text_len": body_len,
        "name_match": _name_token(company_name) in (r.text or "").lower(),
        "http_status": r.http_status,
    }


# Third-party domains that show up in a "<company> pipeline" search but are not the company's
# own pipeline page (news/aggregators/filings). We only trust the company's OWN domain.
_AGGREGATOR_DOMAINS = (
    "sec.gov", "biopharmadive", "fiercebiotech", "patsnap", "synapse", "globenewswire",
    "prnewswire", "businesswire", "wikipedia", "bloomberg", "reuters", "marketwatch",
    "yahoo", "linkedin", "crunchbase", "stockanalysis", "nasdaq", "endpts", "drugs.com",
    "clinicaltrials.gov", "investing.com", "stocktitan",
)


def _on_company_domain(url: str, name_token: str) -> bool:
    """True when the URL is on the company's OWN domain (not a news/aggregator site)."""
    from urllib.parse import urlparse

    netloc = urlparse(url).netloc.lower()
    if any(agg in netloc for agg in _AGGREGATOR_DOMAINS):
        return False
    registrable = ".".join(netloc.replace("www.", "").split(".")[-2:])
    return name_token in registrable or name_token in netloc


def _root_url(url: str) -> str:
    """The company's registrable-domain root, e.g. .../science/x on aligos.com -> https://aligos.com."""
    from urllib.parse import urlparse

    netloc = urlparse(url).netloc.lower().replace("www.", "")
    registrable = ".".join(netloc.split(".")[-2:])
    return f"https://{registrable}"


def resolve_company_source(
    name: str, ticker: str | None = None,
    render_fn=None, search_fn=None, map_fn=None,
) -> dict:
    """Resolve a scrapable pipeline URL by SEARCH then CRAWL — no URL guessing, no LLM:
      (i)  search "<company> pipeline" and content-check the company's own-domain results;
      (ii) if none check out, map the company site for "pipeline" and content-check those.
    Every candidate is content-checked (validate_pipeline_page). Never raises."""
    if render_fn is None:
        from pipeline_intel.ingest.render import render
        render_fn = render
    search = search_fn or (lambda q: firecrawl_search(q, limit=8))
    name_token = _name_token(name)

    out = {
        "company": name, "ticker": ticker,
        "pipeline_url": None, "method": None, "validated": False,
        "signal": None, "candidates": [],
    }

    def _accept(url: str, via: str) -> bool:
        sig = validate_pipeline_page(url, name, render_fn)
        out["candidates"].append({"url": url, "ok": sig["ok"], "via": via})
        if sig["ok"]:
            out.update(pipeline_url=url, method=via, validated=True, signal=sig)
            return True
        return False

    # (i) search "<company> pipeline" -> content-check the company's own-domain results.
    company_root: str | None = None
    seen: set[str] = set()
    try:
        for query in (f"{name} pipeline", f"{name} drug development pipeline"):
            for res in search(query):
                url = res.get("url")
                if not url or url in seen or not _on_company_domain(url, name_token):
                    continue
                seen.add(url)
                company_root = company_root or _root_url(url)
                if _accept(url, "firecrawl_search"):
                    return out
    except FirecrawlError as exc:
        # transient (rate limit / network) — retryable, NOT a genuine "unresolved".
        out.update(transient=True, error=str(exc)[:160])
        return out

    # (ii) map the company site for "pipeline" -> content-check the ranked candidates.
    if company_root:
        mapper = map_fn or (lambda root: firecrawl_map(root, search="pipeline", limit=60))
        try:
            mapped = mapper(company_root)
        except FirecrawlError as exc:
            out.update(transient=True, error=str(exc)[:160])
            return out
        links = [ln for ln in mapped
                 if ln.get("url") and _on_company_domain(ln["url"], name_token)]
        ranked = sorted(links, key=lambda ln: -_score_pipeline_link(ln["url"], ln.get("title") or ""))
        for ln in ranked[:6]:
            if ln["url"] in seen:
                continue
            seen.add(ln["url"])
            if _accept(ln["url"], "firecrawl_map"):
                return out
    return out
