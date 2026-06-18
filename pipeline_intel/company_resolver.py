"""Resolve a company's pipeline source from just its name + ticker — the front of fully
autonomous discovery, so a list of tickers can be scraped hands-off.

Chain (self-contained, no external search API):
  1. An LLM proposes the company's official homepage (+ pipeline URL if it knows it). The
     model reliably knows public pharma domains; this replaces a search engine.
  2. We VALIDATE a candidate by rendering it and checking it actually looks like that
     company's pipeline page (pipeline structure / phase text / pipeline image).
  3. If the LLM's direct pipeline URL doesn't validate, run the homepage-nav finder
     (`url_discovery.discover_pipeline_url`) on the proposed homepage.

Validation is what guards against LLM hallucination: a wrong/dead URL won't render as a
pipeline page, so it's rejected and surfaced for review rather than silently scraped.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from pipeline_intel.extract.client import SONNET_MODEL
from pipeline_intel.ingest.classify import classify_rendered_page, phase_hits
from pipeline_intel.url_discovery import discover_pipeline_url

_PIPELINE_SOURCE_TYPES = {"html_table", "js_cards", "image_page", "pipeline_page"}


class ResolvedSource(BaseModel):
    homepage: str | None = Field(default=None, description="Official corporate homepage, absolute https URL")
    pipeline_url: str | None = Field(
        default=None, description="Drug-pipeline page URL if known, else null. Absolute https URL."
    )
    rationale: str | None = Field(default=None, description="Brief reason / uncertainty note")


def llm_resolve(name: str, ticker: str | None, model: str = SONNET_MODEL) -> ResolvedSource:
    from pipeline_intel.extract.client import get_client

    client = get_client()
    prompt = (
        f"For the pharmaceutical/biotech company '{name}'"
        + (f" (stock ticker {ticker})" if ticker else "")
        + ", give its official corporate homepage URL and the URL of its drug development "
        "pipeline page if you know it. Use the company's real current domain (account for "
        "rebrands/mergers). If unsure of the exact pipeline path, give the homepage and leave "
        "pipeline_url null. Return absolute https URLs only."
    )
    with client.messages.stream(
        model=model, max_tokens=1024,
        messages=[{"role": "user", "content": prompt}], output_format=ResolvedSource,
    ) as stream:
        resp = stream.get_final_message()
    return resp.parsed_output or ResolvedSource(rationale="model returned no parse")


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
    pipeline_signal = kind in _PIPELINE_SOURCE_TYPES and (hits >= 3 or has_image or kind != "pipeline_page")
    return {
        "ok": bool(pipeline_signal),
        "source_type": kind,
        "phase_hits": hits,
        "has_pipeline_image": has_image,
        "name_match": _name_token(company_name) in (r.text or "").lower(),
        "http_status": r.http_status,
    }


def resolve_company_source(
    name: str, ticker: str | None = None, model: str = SONNET_MODEL,
    render_fn=None, llm_fn=None,
) -> dict:
    """Resolve a scrapable pipeline URL for a company from its name + ticker. Returns the
    chosen url + method + validation signal + audit trail (never raises)."""
    if render_fn is None:
        from pipeline_intel.ingest.render import render
        render_fn = render
    guess = (llm_fn or (lambda: llm_resolve(name, ticker, model)))()

    out = {
        "company": name, "ticker": ticker,
        "homepage": guess.homepage, "llm_pipeline_guess": guess.pipeline_url,
        "rationale": guess.rationale,
        "pipeline_url": None, "method": None, "validated": False,
        "signal": None, "candidates": [],
    }

    # 1) trust-but-verify the LLM's direct pipeline URL
    if guess.pipeline_url:
        sig = validate_pipeline_page(guess.pipeline_url, name, render_fn)
        if sig["ok"]:
            out.update(pipeline_url=guess.pipeline_url, method="llm_direct", validated=True, signal=sig)
            return out

    # 2) homepage-nav finder on the proposed homepage
    if guess.homepage:
        disc = discover_pipeline_url(name, guess.homepage, render_fn=render_fn)
        out["candidates"] = disc.get("candidates", [])
        if disc.get("pipeline_url"):
            sig = validate_pipeline_page(disc["pipeline_url"], name, render_fn)
            out.update(pipeline_url=disc["pipeline_url"], method="homepage_nav",
                       validated=sig["ok"], signal=sig)
    return out
