"""Deterministic extractors for parseable pipeline pages.

These run before LLM extraction. If a page exposes structured product/indication/phase
markup, we should parse it directly: cheaper, faster, and more reliable.
"""

from __future__ import annotations

import html
import re

from pipeline_intel.extract.schemas import (
    ExtractedAsset,
    ExtractedProgram,
    ExtractionResult,
    ExtraField,
)

DETERMINISTIC_MODEL = "deterministic-dom-v1"

_MOBILE_BLOCK_RE = re.compile(
    r'<div class="pc" id="pc-mobile-\d+">(.*?)(?=<div class="pc" id="pc-mobile-\d+">|'
    r'<div class="pt-row">\s*<div class="footnote"|</div>\s*</div>\s*</div>\s*</div>)',
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")

TARGET_HINTS = {
    "AAV-ABCA4": "ABCA4",
    "AAV-AIPL1": "AIPL1",
    "AAV-BDNF2": "BDNF",
    "AAV-GAD2": "GAD2",
    "AAV-GAD": "GAD2",
    "AAV-hAQP1": "AQP1",
    "AAV-RDH12": "RDH12",
    "AAV-UPF1, AAV-CNTFR": "UPF1 / CNTFR",
    "AAV-VEGFR2": "VEGFR2",
    "BBS10": "BBS10",
    "Botaretigene sparoparvovec": "RPGR",
}

GENE_THERAPY_PREFIXES = ("AAV-", "Ribo", "GLP-1", "BBS", "Botaretigene", "Undisclosed")

def extract_structured_pipeline(html_text: str, page_text: str | None = None) -> ExtractionResult | None:
    if "pc-mobile-" not in html_text or "Product Candidate" not in html_text:
        return None

    assets: list[ExtractedAsset] = []
    for block in _MOBILE_BLOCK_RE.findall(html_text):
        product = _first_match(r"<h4>(.*?)</h4>", block)
        indication = _first_match(r"<h5>Indication</h5>\s*<p><strong>(.*?)</strong></p>", block)
        phase = _first_match(
            r'<h5 class="phase-label">Phase</h5>\s*<p><strong>(.*?)</strong></p>',
            block,
        )
        if not product or not indication or not phase:
            continue

        product = _clean(product)
        indication = _clean(indication)
        phase = _clean(phase)
        if product.startswith("Botaretigene sparoparvovec"):
            product = "Botaretigene sparoparvovec"
        if product == "AAV-BDNF":
            product = "AAV-BDNF2"
        notes = [_clean(x) for x in re.findall(r'<div class="bar-text">(.*?)</div>', block, re.S)]
        notes = [n for n in notes if n]

        additional_fields = []
        if notes:
            additional_fields.append(ExtraField(name="Designations / notes", value="; ".join(notes)))

        assets.append(
            ExtractedAsset(
                preferred_name=product,
                synonyms=[],
                modality_verbatim="Gene Therapy" if _looks_like_gene_therapy(product) else None,
                target_verbatim=TARGET_HINTS.get(product),
                mechanism_verbatim=None,
                originator_verbatim=None,
                partners=[],
                programs=[
                    ExtractedProgram(
                        indication_verbatim=indication,
                        phase_verbatim=phase,
                        status=None,
                        additional_fields=additional_fields,
                    )
                ],
                additional_fields=[],
            )
        )

    if not assets:
        return None
    return ExtractionResult(
        assets=assets,
        page_notes="Extracted deterministically from structured page HTML.",
    )


def _first_match(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.S)
    return m.group(1) if m else None


def _clean(value: str) -> str:
    value = re.sub(r"<sup>(.*?)</sup>", r"\1", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _looks_like_gene_therapy(product: str) -> bool:
    return product.startswith(GENE_THERAPY_PREFIXES)
