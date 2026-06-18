import io

from PIL import Image

from pipeline_intel.extract.imaging import prepare_screenshots


def test_prepare_screenshots_skips_unopenable_bytes():
    # A linked "image" that is actually HTML/SVG/truncated must not crash extraction.
    assert prepare_screenshots(b"<html>not an image</html>") == []


def test_prepare_screenshots_returns_one_tile_for_small_png():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, format="PNG")
    tiles = prepare_screenshots(buf.getvalue())
    assert len(tiles) == 1
    assert tiles[0][:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
