from pipeline_intel.company_resolver import (
    ResolvedSource,
    _name_token,
    resolve_company_source,
    validate_pipeline_page,
)
from pipeline_intel.ingest.render import RenderResult


def _render(html, text, status=200, images=None):
    def fn(url):
        return RenderResult(url=url, http_status=status, html=html, text=text,
                            screenshot=b"", meta={"pipeline_image_urls": images or []})
    return fn


PIPELINE_HTML = "<table>" + "".join(f"<tr><td>ABC-{i}</td></tr>" for i in range(6)) + "</table>"
PIPELINE_TEXT = "Acme pipeline: Phase 1 Phase 2 Phase 3 Preclinical Filed"


def test_name_token_first_significant_word():
    assert _name_token("Bristol Myers Squibb") == "bristol"
    assert _name_token("GSK plc") == "gsk"


def test_validate_accepts_a_real_pipeline_page():
    sig = validate_pipeline_page("https://acme.com/pipeline", "Acme",
                                 _render(PIPELINE_HTML, PIPELINE_TEXT))
    assert sig["ok"] is True
    assert sig["name_match"] is True


def test_validate_rejects_non_pipeline_or_error():
    assert validate_pipeline_page("https://acme.com/about", "Acme",
                                  _render("<p>about us</p>", "About Acme. Careers."))["ok"] is False
    assert validate_pipeline_page("https://acme.com/x", "Acme",
                                  _render("", "", status=404))["ok"] is False


def test_resolver_returns_llm_direct_when_it_validates():
    def llm():
        return ResolvedSource(homepage="https://acme.com", pipeline_url="https://acme.com/pipeline")
    out = resolve_company_source("Acme", "ACME", render_fn=_render(PIPELINE_HTML, PIPELINE_TEXT), llm_fn=llm)
    assert out["pipeline_url"] == "https://acme.com/pipeline"
    assert out["method"] == "llm_direct"
    assert out["validated"] is True


def test_resolver_falls_back_to_homepage_nav():
    # LLM gives a homepage and a BAD (404) pipeline url; finder recovers it from homepage links.
    home_html = '<a href="/pipeline">Pipeline</a>'

    def render_fn(url):
        if url.rstrip("/") == "https://acme.com":
            return RenderResult(url=url, http_status=200, html=home_html, text="home",
                                screenshot=b"", meta={"pipeline_image_urls": []})
        if url == "https://acme.com/dead":
            return RenderResult(url=url, http_status=404, html="", text="", screenshot=b"", meta={})
        return RenderResult(url=url, http_status=200, html=PIPELINE_HTML, text=PIPELINE_TEXT,
                            screenshot=b"", meta={"pipeline_image_urls": []})

    def llm():
        return ResolvedSource(homepage="https://acme.com", pipeline_url="https://acme.com/dead")
    out = resolve_company_source("Acme", "ACME", render_fn=render_fn, llm_fn=llm)
    assert out["pipeline_url"] == "https://acme.com/pipeline"
    assert out["method"] == "homepage_nav"
    assert out["validated"] is True
