"""Source-type detection — "figure out the extraction method".

Two stages:
  * `sniff_url_type`        : before fetching, decide whether a source URL is a downloadable
                              document (csv/xlsx/pdf) vs a page to render.
  * `classify_rendered_page`: after rendering, classify the page so model routing can react
                              (image-backed pipelines escalate to a stronger model).

The page classes mirror `source_discovery.SOURCE_RANK`. Routing (`model_routing`) already
keys off `CompanySource.source_type`; this is what finally populates it from real evidence.
"""

from __future__ import annotations

import re

from pipeline_intel.ingest.fetch_doc import url_ext
from pipeline_intel.ingest.parse_doc import doc_kind

_KIND_TO_SOURCE_TYPE = {"csv": "csv_doc", "xlsx": "xlsx_doc", "pdf": "pdf_doc"}

# Phase vocabulary that signals the pipeline data is present as readable text.
_PHASE_RE = re.compile(
    r"\b(pre[- ]?clinical|phase\s*[1-4]|phase\s*(?:i{1,3}|iv)\b|ph\s*[1-3]\b"
    r"|registration|filed|approved|marketed|commercial|discovery)\b",
    re.I,
)
_TR_RE = re.compile(r"<tr\b", re.I)
_TABLE_RE = re.compile(r"<table\b", re.I)


def sniff_url_type(url: str, content_type: str | None = None) -> str | None:
    """Return a document source_type (csv_doc/xlsx_doc/pdf_doc) if the URL or content-type
    identifies a downloadable file, else None (treat as a page)."""
    kind = doc_kind(content_type, url_ext(url))
    return _KIND_TO_SOURCE_TYPE.get(kind) if kind else None


def phase_hits(text: str) -> int:
    return len(_PHASE_RE.findall(text or ""))


def classify_rendered_page(
    html: str,
    text: str,
    pipeline_image_urls: list[str] | None = None,
    *,
    min_phase_text: int = 3,
) -> str:
    """Classify a rendered page into image_page | html_table | js_cards | pipeline_page.

    The high-stakes distinction is `image_page` (the pipeline is a chart/graphic with no
    readable phase text → routing escalates). `html_table` is the next clearest signal;
    everything else defaults to the generic `pipeline_page`.
    """
    hits = phase_hits(text)
    has_images = bool(pipeline_image_urls)

    # Pipeline lives in an image when there's a pipeline-looking image and little/no readable
    # phase text to extract from the DOM.
    if has_images and hits < min_phase_text:
        return "image_page"

    tr_count = len(_TR_RE.findall(html or ""))
    if _TABLE_RE.search(html or "") and tr_count >= 4 and hits >= min_phase_text:
        return "html_table"

    # No table but plenty of phase text rendered by JS into repeated card containers.
    if hits >= min_phase_text and tr_count < 4:
        return "js_cards"

    return "pipeline_page"
