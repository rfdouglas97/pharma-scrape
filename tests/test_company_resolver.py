from pipeline_intel.company_resolver import (
    _is_news_url,
    _name_token,
    _on_company_domain,
    resolve_company_source,
    validate_pipeline_page,
)
from pipeline_intel.ingest.render import RenderResult

PIPE_HTML = "<table>" + "".join(f"<tr><td>X-{i}</td></tr>" for i in range(6)) + "</table>"
PIPE_TEXT = "Acme pipeline: Phase 1 Phase 2 Phase 3 Preclinical Filed Approved"  # phase-rich


def _rr(html, text, status=200, images=None):
    def fn(url):
        return RenderResult(url=url, http_status=status, html=html, text=text,
                            screenshot=b"", meta={"pipeline_image_urls": images or []})
    return fn


def test_name_token():
    assert _name_token("Bristol Myers Squibb") == "bristol"
    assert _name_token("GSK plc") == "gsk"


def test_on_company_domain_filters_aggregators():
    assert _on_company_domain("https://www.acme.com/pipeline", "acme") is True
    assert _on_company_domain("https://investor.acme.com/x", "acme") is True
    assert _on_company_domain("https://biopharmadive.com/news/acme", "acme") is False
    assert _on_company_domain("https://sec.gov/filing", "acme") is False


def test_on_company_domain_matches_ticker_domain():
    # many biotechs use a ticker domain (Dogwood Therapeutics -> dwtx.com), which the
    # name token wouldn't match.
    assert _on_company_domain("https://www.dwtx.com/pipeline", "dogwood", "DWTX") is True
    assert _on_company_domain("https://ir.dwtx.com/news", "dogwood", "DWTX") is True
    assert _on_company_domain("https://www.dwtx.com/pipeline", "dogwood", None) is False


def test_is_news_url_blocks_non_pipeline_pages():
    # news / press releases
    assert _is_news_url("https://ir.x.com/news/press-releases/detail/1/foo") is True
    assert _is_news_url("https://x.com/index.php/investors/news-center/press-releases/1") is True
    assert _is_news_url("https://x.com/media/2025/announcement") is True
    # FAQ / SEC filings / admin — all say "Phase 1/2/3" but are not pipeline pages
    assert _is_news_url("https://ir.x.com/resources/faq") is True
    assert _is_news_url("https://ir.x.com/sec-filings/all/content/x_ex99-1.htm") is True
    assert _is_news_url("https://x.com/careers") is True
    # bio / people pages (a scientist's bio is not a pipeline)
    assert _is_news_url("https://imagenebio.com/team/weiguo-su-ph-d") is True
    assert _is_news_url("https://x.com/leadership/jane-doe") is True
    # press-release / article slug (many-hyphen headline) even without a /news/ path
    assert _is_news_url("https://x.com/beyondspring-receives-nasdaq-notice-regarding-bid-price-3") is True
    # real pipeline pages (incl. investor subdomain, short hyphenated paths) are NOT excluded
    assert _is_news_url("https://x.com/pipeline/pipeline-oncology") is False
    assert _is_news_url("https://x.com/clinical-pipeline/") is False
    assert _is_news_url("https://x.com/product-portfolio/pipeline") is False
    assert _is_news_url("https://investor.jnj.com/pipeline/default.aspx") is False


def test_resolver_never_sources_from_news():
    # a press release that WOULD content-validate must still be skipped -> unresolved.
    def search(_q):
        return [{"url": "https://acme.com/news/press-releases/123-acme-phase-2-data"}]
    out = resolve_company_source("Acme", "ACME", render_fn=_rr(PIPE_HTML, PIPE_TEXT),
                                 search_fn=search, map_fn=lambda _r: [])
    assert out["pipeline_url"] is None and out["validated"] is False


def test_validate_accepts_phase_rich_page():
    sig = validate_pipeline_page("https://acme.com/pipeline", "Acme", _rr(PIPE_HTML, PIPE_TEXT))
    assert sig["ok"] is True


def test_validate_rejects_press_release_by_body_markers():
    # an opaque permalink (/2259-2) the URL guardrail can't see — caught by newswire markers
    # in the body even though it mentions Phase 1/2/3.
    pr = "BeyondSpring Phase 1 Phase 2 Phase 3 ... NEW YORK (GLOBE NEWSWIRE) -- the company..."
    sig = validate_pipeline_page("https://beyondspringpharma.com/2259-2", "BeyondSpring",
                                 _rr("<p>x</p>", pr))
    assert sig["ok"] is False and sig["source_type"] == "press_release"


def test_validate_rejects_redirect_to_bare_image_trap():
    # "/pipeline/" 301s to a stray .jpg: browser renders the image with ~no body text.
    sig = validate_pipeline_page("https://acme.com/pipeline/", "Acme", _rr("<img>", "", images=["x.jpg"]))
    assert sig["ok"] is False


def test_validate_rejects_non_pipeline_and_errors():
    about = validate_pipeline_page("https://acme.com/about", "Acme", _rr("<p>x</p>", "About. Careers."))
    assert about["ok"] is False
    assert validate_pipeline_page("https://acme.com/x", "Acme", _rr("", "", status=404))["ok"] is False


def test_resolver_search_finds_pipeline_page():
    def search(_q):
        return [{"url": "https://acme.com/science/pipeline"}]
    out = resolve_company_source("Acme", "ACME", render_fn=_rr(PIPE_HTML, PIPE_TEXT),
                                 search_fn=search, map_fn=lambda _root: [])
    assert out["pipeline_url"] == "https://acme.com/science/pipeline"
    assert out["method"] == "firecrawl_search" and out["validated"] is True


def test_resolver_skips_aggregators_then_maps():
    # search returns a news site (skipped) + the homepage (doesn't validate); map finds the page.
    def search(_q):
        return [{"url": "https://biopharmadive.com/news/acme"}, {"url": "https://acme.com/"}]

    def mp(_root):
        return [{"url": "https://acme.com/programs/pipeline", "title": "Pipeline"}]

    def render(url):
        if url.rstrip("/") == "https://acme.com":
            return RenderResult(url=url, http_status=200, html="<p>home</p>", text="home",
                                screenshot=b"", meta={"pipeline_image_urls": []})
        return RenderResult(url=url, http_status=200, html=PIPE_HTML, text=PIPE_TEXT,
                            screenshot=b"", meta={"pipeline_image_urls": []})

    out = resolve_company_source("Acme", "ACME", render_fn=render, search_fn=search, map_fn=mp)
    assert out["pipeline_url"] == "https://acme.com/programs/pipeline"
    assert out["method"] == "firecrawl_map" and out["validated"] is True


def test_resolver_unresolved_when_nothing_validates():
    out = resolve_company_source("Acme", "ACME", render_fn=_rr("<p>x</p>", "nothing"),
                                 search_fn=lambda _q: [], map_fn=lambda _r: [])
    assert out["pipeline_url"] is None and out["validated"] is False
