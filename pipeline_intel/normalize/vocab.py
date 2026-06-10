"""Normalize verbatim phase/modality strings to controlled-vocabulary codes.

Strategy (thin, M2): deterministic dictionary lookup against vocab_mapping (seeded with
the canonical aliases). A miss returns None, records the verbatim with status='unmapped',
and files a review_queue item — we never guess or silently drop. LLM-assisted mapping of
novel strings is an M4 enhancement; until then unmapped values surface for human review.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import ReviewQueue, VocabMapping

_PARENS = re.compile(r"\([^)]*\)")          # "(EU, JP)" qualifiers
_FOOTNOTE = re.compile(r"[*#†‡§✝~^]+")       # footnote markers
_NOISE = re.compile(r"\b(in progress|achieved|ongoing|status)\b")
_WS = re.compile(r"\s+")


def preclean(verbatim: str) -> str:
    """Strip footnote markers, parenthetical qualifiers, and status-suffix noise so that
    'Phase III *', 'Registration (EU, JP)', and 'Phase 3 in Progress' all reduce to a
    dictionary-matchable core. Preserves the original for the review queue."""
    v = verbatim.lower()
    v = _PARENS.sub(" ", v)
    v = _FOOTNOTE.sub(" ", v)
    v = _NOISE.sub(" ", v)
    return _WS.sub(" ", v).strip()


def _lookup(s: Session, vocab: str, key: str) -> str | None:
    return s.execute(
        select(VocabMapping.code).where(
            VocabMapping.vocab == vocab,
            VocabMapping.verbatim == key,
            VocabMapping.code.isnot(None),
        )
    ).scalar_one_or_none()


def normalize(s: Session, vocab: str, verbatim: str | None) -> str | None:
    """Map a verbatim value to a vocab code. None in -> None out (nothing to map).

    On a miss: persist an 'unmapped' vocab_mapping row (so the same string isn't
    re-queued every run) and a review_queue item, then return None."""
    if not verbatim or not verbatim.strip():
        return None
    key = preclean(verbatim)

    code = _lookup(s, vocab, key)
    if code is not None:
        return code

    # Miss: has it already been recorded as unmapped? (keyed on the precleaned core)
    existing = s.execute(
        select(VocabMapping).where(VocabMapping.vocab == vocab, VocabMapping.verbatim == key)
    ).scalar_one_or_none()
    if existing is None:
        s.add(VocabMapping(vocab=vocab, verbatim=key, code=None, status="unmapped"))
        s.add(
            ReviewQueue(
                kind="vocab_unmapped",
                entity_ref={"vocab": vocab},
                payload={"verbatim": verbatim},
            )
        )
    return None


def normalize_phase(s: Session, verbatim: str | None) -> str | None:
    return normalize(s, "phase", verbatim)


def normalize_modality(s: Session, verbatim: str | None) -> str | None:
    return normalize(s, "modality", verbatim)
