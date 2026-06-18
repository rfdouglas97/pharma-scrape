from pipeline_intel.source_discovery import discover_from_html, select_promotable_file


def _cand(url, source_type, rank):
    return {"url": url, "source_type": source_type, "preferred_source_rank": rank}


def test_promotes_pipeline_pdf_over_image_page():
    cands = [
        _cand("https://x.com/files/2026-pipeline.pdf", "pdf_doc", 20),
        _cand("https://x.com/pipeline", "pipeline_page", 50),
    ]
    choice = select_promotable_file(cands, "image_page")  # page rank 70
    assert choice is not None
    assert choice["url"].endswith("2026-pipeline.pdf")


def test_prefers_xlsx_over_pdf_when_both_present():
    cands = [
        _cand("https://x.com/pipeline.pdf", "pdf_doc", 20),
        _cand("https://x.com/product-pipeline.xlsx", "xlsx_doc", 10),
    ]
    choice = select_promotable_file(cands, "html_table")
    assert choice["source_type"] == "xlsx_doc"


def test_does_not_promote_unrelated_pdf():
    # an annual report PDF linked on the page is not a pipeline file
    cands = [_cand("https://x.com/2026-annual-report.pdf", "pdf_doc", 20)]
    assert select_promotable_file(cands, "html_table") is None


def test_does_not_promote_when_page_already_outranks_file():
    # a clean html_table (rank 35) should not be replaced by a worse-ranked... (files always
    # outrank pages here, so test the inverse: no doc candidates at all)
    cands = [_cand("https://x.com/pipeline", "pipeline_page", 50)]
    assert select_promotable_file(cands, "html_table") is None


def test_discover_then_select_end_to_end():
    html = '<a href="/docs/rd-pipeline.xlsx">download</a><a href="/about">about</a>'
    cands = discover_from_html("https://x.com/pipeline", html)
    choice = select_promotable_file(cands, "image_page")
    assert choice is not None and choice["source_type"] == "xlsx_doc"
