from pipeline_intel.ingest.render import robots_allows
from pipeline_intel.ingest.run import _slug


def test_slug():
    assert _slug("Bristol Myers Squibb") == "bristol-myers-squibb"


def test_robots_allows_with_crawl_delay_preamble(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"Crawl-delay: 10\nUser-agent: *\nDisallow:\n"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    assert robots_allows("https://example.com/pipeline", "PipelineIntelBot/0.1")
    assert _slug("Johnson & Johnson") == "johnson-johnson"
    assert _slug("Eli Lilly") == "eli-lilly"
