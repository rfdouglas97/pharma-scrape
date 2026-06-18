from pipeline_intel.url_discovery import (
    extract_links,
    rank_pipeline_candidates,
    score_pipeline_link,
)

BASE = "https://www.glpg.com"


def test_extract_links_absolutizes_and_cleans_text():
    html = '<a href="/pipeline">Our <b>Pipeline</b></a><a href="https://x.com/a">x</a>'
    links = extract_links(BASE, html)
    urls = {ln["url"]: ln["text"] for ln in links}
    assert urls["https://www.glpg.com/pipeline"] == "Our Pipeline"


def test_score_prefers_pipeline_in_path_and_text():
    assert score_pipeline_link(f"{BASE}/pipeline", "Pipeline", BASE) >= 190
    assert score_pipeline_link(f"{BASE}/science", "Our Science", BASE) > 0
    # off-domain links are rejected
    assert score_pipeline_link("https://other.com/pipeline", "Pipeline", BASE) == -1


def test_rank_picks_pipeline_link_over_noise():
    html = (
        '<a href="/careers">Careers</a>'
        '<a href="/science/pipeline">Pipeline</a>'
        '<a href="/investors">Investors</a>'
        '<a href="/our-science">Our Science</a>'
    )
    ranked = rank_pipeline_candidates(BASE, html)
    assert ranked[0].url == "https://www.glpg.com/science/pipeline"
    assert ranked[0].score >= 100


def test_rank_returns_empty_when_no_pipeline_links():
    html = '<a href="/careers">Careers</a><a href="/contact">Contact</a>'
    assert rank_pipeline_candidates(BASE, html) == []
