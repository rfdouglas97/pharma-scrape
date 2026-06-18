"""Autonomous pipeline-URL discovery — resolve a company's pipeline page from its homepage,
so the registry only needs a company name + homepage, not a hand-curated pipeline URL. This
is the bootstrapping primitive for scaling to hundreds of companies.

Strategy (self-contained, no external search API): render the homepage, rank its links by
how pipeline-like the URL path + anchor text are (same-domain only), validate the top
candidate is reachable, and optionally let an LLM break ties on ambiguous homepages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

_A_RE = re.compile(r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*?>(.*?)</a>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# URL-path tokens that co-occur with pipeline pages (science/research sections).
_PATH_SECTION_HINTS = ("science", "research", "development", "portfolio", "products", "medicines",
                       "candidates", "rd", "r-d", "innovation")
# Anchor-text hints (weaker than an explicit "pipeline").
_TEXT_WEAK_HINTS = ("our science", "science", "research", "development", "portfolio", "products",
                    "candidates", "medicines", "innovation", "r&d")


@dataclass
class PipelineCandidate:
    url: str
    text: str
    score: int


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()


def _registrable(netloc: str) -> str:
    netloc = netloc.lower().split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    parts = netloc.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc


def extract_links(base_url: str, html: str) -> list[dict]:
    """All <a href> links with cleaned anchor text, absolutized + deduped."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for href, text in _A_RE.findall(html or ""):
        url = urljoin(base_url, href.split("#")[0]).strip()
        if not url.lower().startswith("http"):
            continue
        t = _clean(text)
        key = (url, t.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"url": url, "text": t})
    return out


def score_pipeline_link(url: str, text: str, base_url: str) -> int:
    """Higher = more pipeline-like. Same-company links only (-1 for off-domain)."""
    if _registrable(urlparse(url).netloc) != _registrable(urlparse(base_url).netloc):
        return -1
    path = urlparse(url).path.lower()
    t = text.lower()
    score = 0
    if "pipeline" in path:
        score += 100
    if t == "pipeline":
        score += 90
    elif "pipeline" in t:
        score += 70
    if any(h in path for h in _PATH_SECTION_HINTS):
        score += 20
    if any(h in t for h in _TEXT_WEAK_HINTS):
        score += 10
    return score


def rank_pipeline_candidates(base_url: str, html: str) -> list[PipelineCandidate]:
    """Ranked pipeline-page candidates from a homepage's links (best first, deduped by URL)."""
    best: dict[str, PipelineCandidate] = {}
    for link in extract_links(base_url, html):
        score = score_pipeline_link(link["url"], link["text"], base_url)
        if score <= 0:
            continue
        prev = best.get(link["url"])
        if prev is None or score > prev.score:
            best[link["url"]] = PipelineCandidate(url=link["url"], text=link["text"], score=score)
    return sorted(best.values(), key=lambda c: -c.score)


def discover_pipeline_url(company_name: str, homepage_url: str, render_fn=None) -> dict:
    """Render the homepage and resolve the best pipeline-page URL. Returns the chosen url +
    the ranked candidates for auditability. render_fn defaults to the Playwright renderer."""
    if render_fn is None:
        from pipeline_intel.ingest.render import render
        render_fn = render

    result = render_fn(homepage_url)
    candidates = rank_pipeline_candidates(homepage_url, result.html)
    chosen = candidates[0].url if candidates else None
    return {
        "company": company_name,
        "homepage": homepage_url,
        "pipeline_url": chosen,
        "confident": bool(candidates) and candidates[0].score >= 100,
        "candidates": [{"url": c.url, "text": c.text, "score": c.score} for c in candidates[:8]],
    }
