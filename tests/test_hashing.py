from pipeline_intel.ingest.hashing import content_hash, normalize_text


def test_normalize_collapses_whitespace():
    assert normalize_text("  a\n\n  b\t c  ") == "a b c"


def test_hash_stable_across_cosmetic_whitespace():
    # The whole point of hashing visible text: markup/whitespace churn must not flip it.
    a = content_hash("Phase 2   NSCLC\n\n")
    b = content_hash("Phase 2 NSCLC")
    assert a == b


def test_hash_changes_on_content_change():
    assert content_hash("Phase 2 NSCLC") != content_hash("Phase 3 NSCLC")


def test_hash_is_sha256_hex():
    h = content_hash("anything")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
