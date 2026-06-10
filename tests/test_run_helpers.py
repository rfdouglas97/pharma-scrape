from pipeline_intel.ingest.run import _slug


def test_slug():
    assert _slug("Bristol Myers Squibb") == "bristol-myers-squibb"
    assert _slug("Johnson & Johnson") == "johnson-johnson"
    assert _slug("Eli Lilly") == "eli-lilly"
