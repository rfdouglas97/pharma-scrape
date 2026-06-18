from pipeline_intel.source_discovery import classify_url, discover_from_html, rank_for_source_type


def test_source_type_ranking_prefers_structured_files():
    assert rank_for_source_type("xlsx_doc") < rank_for_source_type("pdf_doc")
    assert rank_for_source_type("pdf_doc") < rank_for_source_type("pipeline_page")


def test_classify_url_by_extension():
    assert classify_url("https://example.com/pipeline.xlsx") == "xlsx_doc"
    assert classify_url("https://example.com/pipeline.pdf?download=1") == "pdf_doc"


def test_discover_from_html_keeps_pipeline_and_file_links():
    html = """
    <a href="/investors/pipeline.xlsx">Pipeline file</a>
    <a href="/science/pipeline">Pipeline page</a>
    <a href="/news">News</a>
    """
    candidates = discover_from_html("https://example.com/root", html)
    urls = [c["url"] for c in candidates]
    assert urls == [
        "https://example.com/investors/pipeline.xlsx",
        "https://example.com/science/pipeline",
    ]
    assert candidates[0]["source_type"] == "xlsx_doc"
