"""Content hashing for the hash-skip optimization that makes weekly cadence affordable.

We hash the *visible text* of the rendered DOM (not raw HTML) so that cosmetic markup
churn — changing wrapper divs, rotating ad/session tokens, reordered attributes — does
not look like a pipeline change. Only a change in displayed content flips the hash.
"""

from __future__ import annotations

import hashlib
import re

_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Collapse whitespace and strip, so trivial formatting differences don't change the hash."""
    return _WS.sub(" ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
