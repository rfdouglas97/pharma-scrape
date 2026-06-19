"""Firecrawl render-fallback: when Playwright errors or returns a JS-empty page, re-render via
Firecrawl. Exercises render_with_fallback + the resolver validation path without a real browser."""

import pipeline_intel.ingest.render as render_mod
from pipeline_intel.company_resolver import validate_pipeline_page
from pipeline_intel.ingest.render import RenderError, RenderResult, render_with_fallback

PHASE_RICH = "Acme pipeline: Phase 1 Phase 2 Phase 3 Preclinical"  # phase_hits >= 3
EMPTY = "Loading..."  # JS shell, phase_hits == 0


class _FakeSettings:
    def __init__(self, *, key="fc-key", enabled=True):
        self.firecrawl_api_key = key
        self.firecrawl_render_fallback = enabled
        self.firecrawl_fallback_wait_ms = 8000


def _rr(text, images=None):
    return RenderResult(url="https://acme.com/pipeline", http_status=200, html=text, text=text,
                        screenshot=b"x", meta={"pipeline_image_urls": images or []})


def _wire(monkeypatch, *, playwright, firecrawl, settings=None):
    """playwright: a value to return, or an Exception to raise. firecrawl: markdown str or None."""
    def fake_render(url, cfg=None):
        if isinstance(playwright, Exception):
            raise playwright
        return playwright
    monkeypatch.setattr(render_mod, "render", fake_render)
    monkeypatch.setattr(render_mod, "settings", lambda: settings or _FakeSettings())
    calls = {"n": 0}

    def fake_scrape(url, wait_ms=4000):
        calls["n"] += 1
        return firecrawl
    monkeypatch.setattr("pipeline_intel.firecrawl_client.firecrawl_scrape", fake_scrape)
    return calls


def test_render_error_falls_back_to_firecrawl(monkeypatch):
    _wire(monkeypatch, playwright=RenderError("nav timeout"), firecrawl=PHASE_RICH)
    r = render_with_fallback("https://acme.com/pipeline")
    assert r.meta.get("render_via") == "firecrawl"
    assert "Phase 1" in r.text


def test_poor_render_rescued_when_firecrawl_richer(monkeypatch):
    _wire(monkeypatch, playwright=_rr(EMPTY), firecrawl=PHASE_RICH)
    r = render_with_fallback("https://acme.com/pipeline")
    assert r.meta.get("render_via") == "firecrawl"


def test_rich_render_does_not_call_firecrawl(monkeypatch):
    calls = _wire(monkeypatch, playwright=_rr(PHASE_RICH), firecrawl=PHASE_RICH)
    r = render_with_fallback("https://acme.com/pipeline")
    assert r.meta.get("render_via") is None  # kept the Playwright result
    assert calls["n"] == 0  # guard held — no API call


def test_fallback_disabled_is_passthrough(monkeypatch):
    calls = _wire(monkeypatch, playwright=_rr(EMPTY), firecrawl=PHASE_RICH,
                  settings=_FakeSettings(enabled=False))
    r = render_with_fallback("https://acme.com/pipeline")
    assert r.text == EMPTY and calls["n"] == 0


def test_no_api_key_is_passthrough(monkeypatch):
    calls = _wire(monkeypatch, playwright=_rr(EMPTY), firecrawl=PHASE_RICH,
                  settings=_FakeSettings(key=None))
    r = render_with_fallback("https://acme.com/pipeline")
    assert r.text == EMPTY and calls["n"] == 0


def test_render_error_with_no_firecrawl_reraises(monkeypatch):
    _wire(monkeypatch, playwright=RenderError("nav timeout"), firecrawl=None)
    try:
        render_with_fallback("https://acme.com/pipeline")
        raise AssertionError("expected RenderError to propagate")
    except RenderError:
        pass


def test_validate_pipeline_page_resolves_js_page_via_fallback(monkeypatch):
    # The unresolved-fix path: Playwright renders the candidate empty, Firecrawl makes it
    # phase-rich, so validate_pipeline_page (using render_with_fallback) returns ok=True.
    _wire(monkeypatch, playwright=_rr(EMPTY), firecrawl=PHASE_RICH)
    sig = validate_pipeline_page("https://acme.com/pipeline", "Acme", render_with_fallback)
    assert sig["ok"] is True
