"""Screenshot preparation for vision extraction.

The Claude API rejects any image dimension over 8000px, and full-page screenshots of
long pipeline pages routinely exceed that. We tile tall screenshots into vertical slices
sized near Opus 4.8's high-resolution sweet spot (~2576px long edge), with a small
overlap so a pipeline row straddling a tile boundary is never lost. Over-wide images are
downscaled first. This runs at extraction time against the immutable full-page artifact,
so bronze keeps the true source and re-extraction stays reproducible.
"""

from __future__ import annotations

import io

# Each tile's height. Near the high-res processing ceiling so dense tables stay legible
# without ballooning the tile count.
TILE_HEIGHT = 2400
OVERLAP = 150
MAX_TILES = 16
MAX_WIDTH = 2576  # downscale wider screenshots to the high-res long-edge target


def prepare_screenshots(png_bytes: bytes) -> list[bytes]:
    """Return one or more PNG tiles, each within API dimension limits and sized for
    high-res vision. Short screenshots pass through unchanged (after any width downscale)."""
    from PIL import Image  # noqa: PLC0415 — optional dep, only needed when extracting

    img = Image.open(io.BytesIO(png_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size

    if w > MAX_WIDTH:
        scale = MAX_WIDTH / w
        img = img.resize((MAX_WIDTH, int(h * scale)))
        w, h = img.size

    if h <= TILE_HEIGHT:
        return [_to_png(img)]

    tiles: list[bytes] = []
    y = 0
    step = TILE_HEIGHT - OVERLAP
    while y < h and len(tiles) < MAX_TILES:
        tiles.append(_to_png(img.crop((0, y, w, min(y + TILE_HEIGHT, h)))))
        y += step
    return tiles


def _to_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
