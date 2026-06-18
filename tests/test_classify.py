from pipeline_intel.ingest.classify import classify_rendered_page, phase_hits, sniff_url_type
from pipeline_intel.ingest.fetch_doc import DocFetchError, _read_capped, url_ext


def test_sniff_url_type_by_extension():
    assert sniff_url_type("https://x.com/pipeline.xlsx") == "xlsx_doc"
    assert sniff_url_type("https://x.com/data.csv?v=2") == "csv_doc"
    assert sniff_url_type("https://x.com/pipeline.pdf") == "pdf_doc"
    assert sniff_url_type("https://x.com/pipeline") is None


def test_sniff_url_type_by_content_type_when_ext_missing():
    assert sniff_url_type("https://x.com/download", content_type="application/pdf") == "pdf_doc"
    assert sniff_url_type("https://x.com/download", content_type="text/html") is None


def test_url_ext():
    assert url_ext("https://x.com/a/b/file.XLSX?q=1#f") == ".xlsx"
    assert url_ext("https://x.com/nofile") is None


def test_read_capped_enforces_limit():
    assert _read_capped([b"ab", b"cd"], cap=10) == b"abcd"
    try:
        _read_capped([b"a" * 6, b"b" * 6], cap=10)
    except DocFetchError as exc:
        assert "cap" in str(exc)
    else:
        raise AssertionError("expected DocFetchError when cap exceeded")


def test_classify_image_page_when_image_and_no_phase_text():
    html = '<html><body><img class="pipeline" src="chart.png"></body></html>'
    text = "Our pipeline\nContact us"
    assert classify_rendered_page(html, text, ["https://x.com/chart.png"]) == "image_page"


def test_classify_html_table():
    rows = "".join(f"<tr><td>ABC-{i}</td><td>Phase 2</td></tr>" for i in range(6))
    html = f"<table>{rows}</table>"
    text = "ABC-1 Phase 2 Preclinical Phase 1 Phase 3 Filed"
    assert classify_rendered_page(html, text, []) == "html_table"


def test_classify_js_cards_when_phase_text_but_no_table():
    html = "<div class='card'>ABC-1</div>" * 5
    text = "Preclinical Phase 1 Phase 2 Phase 3 Approved"
    assert classify_rendered_page(html, text, []) == "js_cards"


def test_classify_defaults_to_pipeline_page():
    assert classify_rendered_page("<html></html>", "About us. Careers.", []) == "pipeline_page"


def test_phase_hits_counts_vocabulary():
    assert phase_hits("Phase 1, Phase 2 and Preclinical; later Filed") >= 4
