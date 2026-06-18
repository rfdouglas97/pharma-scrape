from pipeline_intel.ingest.fetch_doc import _local_path, fetch_document, url_ext


def test_local_path_resolves_existing_relative_file(tmp_path):
    f = tmp_path / "pipeline.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    assert _local_path(str(f)) == f
    assert _local_path(f"file://{f}") == f


def test_local_path_none_for_http_or_missing():
    assert _local_path("https://x.com/a.pdf") is None
    assert _local_path("/no/such/file.pdf") is None


def test_fetch_document_reads_local_file(tmp_path):
    f = tmp_path / "doc.csv"
    f.write_bytes(b"Asset,Phase\nABC,Phase 2\n")
    doc = fetch_document(str(f))
    assert doc.http_status == 200
    assert doc.ext == ".csv"
    assert b"ABC" in doc.raw_bytes
    assert url_ext(str(f)) == ".csv"
