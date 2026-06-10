"""Map indication strings to MONDO/EFO disease CURIEs.

Pipeline indications are messy free text ("2L+ Multiple myeloma combination with Pomalyst",
"Eosinophilic granulomatosis with polyangiitis (EGPA)", "Refractory chronic cough"). Plain
ontology search on these returns nothing, so the strategy is:
  1. fast exact path: deterministic clean -> MONDO exact-label match;
  2. otherwise LLM normalizes the indication to a canonical disease NAME (stripping line-of-
     therapy, combo partners, qualifiers) -> search MONDO with that clean name -> accept the
     best disease hit, confidence from match quality.
Low confidence / no candidate / not-a-disease -> stored needs-review. We flag, never guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import Indication, IndicationMapping, ReviewQueue
from pipeline_intel.ontology import ols_client

CONFIDENCE_THRESHOLD = 0.8
MAPPER_MODEL = "claude-sonnet-4-6"  # cost-efficient for label normalization

_PARENS = re.compile(r"\s*\([^)]*\)")
_COMBO = re.compile(r"\s+(in\s+)?combination with .*$", re.I)
_LOT = re.compile(r"^\s*(\d+l\+?|first[- ]line|second[- ]line|third[- ]line)\s+", re.I)
_GEO = re.compile(r",\s+(adults?|patients?|in\b).*$", re.I)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def clean_label(label: str) -> str:
    """Cheap deterministic strip of parentheticals, combo tails, line-of-therapy, and
    population/geography — enough for the exact-match fast path."""
    s = _PARENS.sub("", label)
    s = _COMBO.sub("", s)
    s = _GEO.sub("", s)
    prev = None
    while prev != s:
        prev = s
        s = _LOT.sub("", s)
    return s.strip(" ,;")


# Staging/grade/resectability words that block strict OLS token matching — stripped on retry.
_STAGE_WORDS = (
    "extensive-stage", "extensive stage", "limited-stage", "limited stage", "locally advanced",
    "advanced", "metastatic", "recurrent", "relapsed", "refractory", "newly diagnosed",
    "unresectable", "unresected", "resectable", "neoadjuvant", "adjuvant", "peri-operative",
    "perioperative", "previously untreated", "previously treated", "early-stage", "early stage",
    "high-risk", "high risk", "low-risk",
)


def lenient_search(term: str, rows: int = 4) -> list:
    """Search MONDO for a disease term; if strict token matching returns nothing, strip
    staging/grade qualifier words and retry (OLS requires all query tokens to match)."""
    cands = ols_client.search_disease(term, rows=rows)
    if cands:
        return cands
    reduced = term.lower()
    for w in _STAGE_WORDS:
        reduced = reduced.replace(w, " ")
    reduced = " ".join(reduced.split())
    if reduced and reduced != term.lower():
        return ols_client.search_disease(reduced, rows=rows)
    return []


class Normalization(BaseModel):
    disease_name: str | None = Field(
        description="The canonical core disease name, stripped of line-of-therapy, combination "
        "partners, biomarker/population qualifiers. Null if the indication is not a mappable disease "
        "(e.g. 'healthy volunteers', a procedure, or undisclosed)."
    )
    confidence: float = Field(description="0.0-1.0 confidence in this normalization")


class Adjudication(BaseModel):
    chosen_curie: str | None = Field(
        description="CURIE of the candidate that best represents the indication's core disease, "
        "or null if none is a correct match (e.g. all candidates are too specific/wrong, or the "
        "indication is a broad category with no single right disease)."
    )
    confidence: float = Field(description="0.0-1.0 confidence the chosen CURIE is correct")


_NORMALIZE_SYSTEM = """\
You normalize a drug-development indication (messy pharma free-text) to the CANONICAL disease \
name as it appears in a medical disease ontology (MONDO), suitable for exact lookup.

Strip everything that is not part of the ontology disease name:
- line-of-therapy ('1L', '2L+'), combination partners ('combination with X'), biomarker/
  population/geography qualifiers, formulation notes;
- STAGING, GRADE, and resectability words: 'extensive-stage', 'limited-stage', 'advanced',
  'locally advanced', 'metastatic', 'recurrent', 'relapsed', 'refractory', 'newly diagnosed',
  'unresectable', 'neoadjuvant', 'adjuvant', 'early-stage', 'high-risk'.
Use the base ontology name: e.g. 'glioblastoma multiforme' -> 'glioblastoma'; 'extensive-stage \
small cell lung cancer' -> 'small cell lung carcinoma'; 'metastatic castration-resistant \
prostate cancer' -> 'prostate cancer'. KEEP modifiers only when they name a genuinely distinct \
ontology disease (e.g. 'non-small cell lung carcinoma', 'triple-negative breast cancer').

If the indication is not a mappable disease (healthy volunteers, prophylaxis of a non-disease \
state, a pure procedure), return null. Return the disease name in lower case."""


@dataclass
class MapStats:
    mapped_exact: int = 0
    mapped_llm: int = 0
    needs_review: int = 0
    not_a_disease: int = 0
    skipped_existing: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


_ADJUDICATE_SYSTEM = """\
You select the disease ontology term that best represents the CORE disease of a drug \
indication. You are given the original (messy) indication and a list of candidate MONDO \
terms. Pick the CURIE whose disease the indication actually targets — prefer the term at \
the right level of specificity (e.g. for 'colon cancer' choose 'colorectal cancer' over a \
sub-site like 'sigmoid colon cancer'; for a specific subtype indication choose that subtype). \
Return null if no candidate is correct, or if the indication is a broad basket category \
('solid tumors', 'advanced cancer') with no single right disease. Be calibrated."""


def _normalize(label: str, model: str) -> Normalization:
    from pipeline_intel.extract.client import get_client

    client = get_client()
    resp = client.messages.parse(
        model=model,
        max_tokens=500,
        system=_NORMALIZE_SYSTEM,
        messages=[{"role": "user", "content": f"Indication: {label!r}\nReturn the core disease name."}],
        output_format=Normalization,
    )
    return resp.parsed_output


def _adjudicate(label: str, cands: list, model: str) -> Adjudication:
    from pipeline_intel.extract.client import get_client

    cand_lines = "\n".join(f"- {c.curie}: {c.label}" for c in cands)
    client = get_client()
    resp = client.messages.parse(
        model=model,
        max_tokens=500,
        system=_ADJUDICATE_SYSTEM,
        messages=[{"role": "user", "content":
                   f"Indication: {label!r}\n\nCandidate terms:\n{cand_lines}\n\n"
                   "Choose the best CURIE for the core disease, or null."}],
        output_format=Adjudication,
    )
    return resp.parsed_output


def _store(s: Session, indication_id: str, cand, conf: float, method: str, status: str) -> None:
    s.add(IndicationMapping(
        indication_id=indication_id,
        ontology=cand.curie.split(":")[0] if cand else "MONDO",
        curie=cand.curie if cand else None, label=cand.label if cand else None,
        confidence=conf, method=method, status=status,
    ))


def map_indication(s: Session, indication: Indication, model: str = MAPPER_MODEL) -> str:
    # Skip already-resolved; clear any prior unmapped row so a retry is clean.
    existing = s.execute(
        select(IndicationMapping).where(IndicationMapping.indication_id == indication.indication_id)
    ).scalars().all()
    if any(m.status in ("auto", "reviewed") for m in existing):
        return "skipped_existing"
    if existing:
        s.execute(delete(IndicationMapping).where(
            IndicationMapping.indication_id == indication.indication_id))

    label = indication.preferred_label
    cleaned = clean_label(label)

    # 1) exact disease-label match
    exact = ols_client.best_exact(cleaned) or ols_client.best_exact(label)
    if exact is not None:
        _store(s, indication.indication_id, exact, 0.99, "exact", "auto")
        return "mapped_exact"

    # 2) LLM normalize -> MONDO search
    norm = _normalize(label, model)
    if not norm.disease_name:
        _store(s, indication.indication_id, None, 0.0, "llm_assisted", "unmapped")
        s.add(ReviewQueue(kind="ontology_lowconf", entity_ref={"indication_id": indication.indication_id},
                          payload={"label": label, "reason": "LLM: not a mappable disease"}))
        return "not_a_disease"

    cands = lenient_search(norm.disease_name) or lenient_search(cleaned)
    if not cands:
        _store(s, indication.indication_id, None, 0.0, "llm_assisted", "unmapped")
        s.add(ReviewQueue(
            kind="ontology_lowconf", entity_ref={"indication_id": indication.indication_id},
            payload={"label": label, "normalized": norm.disease_name, "reason": "no candidates"}))
        return "needs_review"

    # Fast path: the canonical name exactly matches a candidate label — unambiguous, no
    # second LLM call needed.
    nn = _NON_ALNUM.sub("", norm.disease_name.lower())
    exact_cand = next((c for c in cands if _NON_ALNUM.sub("", c.label.lower()) == nn), None)
    if exact_cand is not None:
        _store(s, indication.indication_id, exact_cand, 0.95, "llm_assisted", "auto")
        return "mapped_llm"

    # 3) LLM adjudicates the candidate set against the original indication.
    adj = _adjudicate(label, cands, model)
    chosen = next((c for c in cands if adj.chosen_curie and c.curie == adj.chosen_curie), None)
    conf = float(adj.confidence)

    if chosen is None or conf < CONFIDENCE_THRESHOLD:
        _store(s, indication.indication_id, chosen, conf, "llm_assisted", "unmapped")
        s.add(ReviewQueue(
            kind="ontology_lowconf", entity_ref={"indication_id": indication.indication_id},
            payload={"label": label, "normalized": norm.disease_name,
                     "candidate": adj.chosen_curie, "confidence": conf},
        ))
        return "needs_review"

    _store(s, indication.indication_id, chosen, conf, "llm_assisted", "auto")
    return "mapped_llm"


def map_all(
    s: Session, model: str = MAPPER_MODEL, limit: int | None = None, commit_every: int = 10
) -> MapStats:
    """Map every indication, committing every `commit_every` so progress is durable and the
    run is resumable (already-mapped indications are skipped on re-run)."""
    stats = MapStats()
    q = select(Indication)
    if limit:
        q = q.limit(limit)
    indications = list(s.execute(q).scalars())
    for i, ind in enumerate(indications, 1):
        outcome = map_indication(s, ind, model)
        setattr(stats, outcome, getattr(stats, outcome) + 1)
        if i % commit_every == 0:
            s.commit()
    s.commit()
    return stats
