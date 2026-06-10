from pipeline_intel.ingest.storage import LocalStorage


def test_local_storage_roundtrip(tmp_path):
    store = LocalStorage(str(tmp_path))
    key = store.put("pfizer/2026-01-01/abc/page.txt", b"hello", "text/plain")
    assert key == "pfizer/2026-01-01/abc/page.txt"
    assert store.get(key) == b"hello"
    assert (tmp_path / key).exists()
