"""Canonicalize a partner/collaborator company name so formatting variants collapse but genuinely
different companies stay distinct — e.g. 'Janssen Pharmaceuticals, Inc.' and 'Janssen' → 'Janssen'
(then aliased to 'Johnson & Johnson'), while 'Acceleron' and 'Merck' stay separate so a real
licensing handoff still surfaces as a change. Returns a readable canonical form (not a lowercase key)."""

from __future__ import annotations

import re

_PARENS = re.compile(r"\([^)]*\)")
_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|pharmaceuticals?|pharma|therapeutics|biopharma|"
    r"biosciences?|sa|ag|gmbh|plc|nv|monoclonals|a johnson . johnson company)\b\.?",
    re.I,
)
_WS = re.compile(r"\s+")
# Alias map keyed by the cleaned (suffix-stripped, lowercased) form -> canonical display name.
_ALIAS = {"janssen": "Johnson & Johnson", "j j": "Johnson & Johnson", "gemoab": "Avencell"}


def normalize_partner(name: str | None) -> str | None:
    if not name or not name.strip():
        return None
    n = _PARENS.sub(" ", name)
    n = _SUFFIX.sub(" ", n)
    n = re.sub(r"[.,]", " ", n)
    n = _WS.sub(" ", n).strip()
    key = re.sub(r"[^a-z0-9 ]", " ", n.lower())
    key = _WS.sub(" ", key).strip()
    if key in _ALIAS:
        return _ALIAS[key]
    return n or None
