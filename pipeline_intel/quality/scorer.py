"""Field-level precision/recall scorer for extraction quality — the M1 gate.

Both predicted and gold ExtractionResults are decomposed into a set of comparable
"facts". Comparing the sets gives true positives / false positives / false negatives,
hence precision and recall, broken down by fact category so we can see *what* the model
gets wrong (e.g. great on assets, weak on targets).

Asset identity is matched across predicted/gold by name+synonym overlap (normalized),
since the same asset can be named differently. Phase/indication comparison is normalized
for whitespace/case/punctuation only — the dataset stores verbatim, but the eval should
not punish "Ph2" vs "Phase 2" as a *capture* error (that is a normalization concern, M2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline_intel.extract.schemas import ExtractionResult
from pipeline_intel.normalize.vocab import preclean

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Phase synonyms collapsed so "Ph2"/"Phase II"/"Phase 2"/"Phase 2 in Progress" all count
# as the same phase. We run the loader's `preclean` first (strips "in progress",
# parentheticals, footnote markers), then canonicalize roman/arabic + registration/approval
# wording — so the eval scores *phase identity*, not verbatim phrasing differences.
_PHASE_CANON = {
    "preclinical": "preclinical", "ind": "preclinical",
    "phase1": "phase1", "phasei": "phase1", "ph1": "phase1",
    "phase2": "phase2", "phaseii": "phase2", "ph2": "phase2",
    "phase3": "phase3", "phaseiii": "phase3", "ph3": "phase3",
    "phase12": "phase12",   # "Phase 1/2"
    "phase23": "phase23",   # "Phase 2/3"
    "filed": "filed", "registration": "filed", "submitted": "filed",
    "approved": "approved", "marketed": "approved", "commercial": "approved",
    "regulatoryapproval": "approved",
    "discontinued": "discontinued", "removed": "discontinued", "terminated": "discontinued",
}


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return _NON_ALNUM.sub("", s.lower())


def _norm_phase(s: str | None) -> str:
    if not s:
        return ""
    n = _norm(preclean(s))  # preclean strips "in progress"/parens/footnotes, then canonicalize
    return _PHASE_CANON.get(n, n)


def _asset_keys(asset) -> set[str]:
    keys = {_norm(asset.preferred_name)}
    keys |= {_norm(x) for x in asset.synonyms}
    return {k for k in keys if k}


def _facts_for_asset(asset_key: str, asset) -> set[tuple]:
    """Decompose one asset into category-tagged facts keyed by the matched asset_key."""
    facts: set[tuple] = {("asset", asset_key)}
    if asset.modality_verbatim:
        facts.add(("modality", asset_key, _norm(asset.modality_verbatim)))
    if asset.target_verbatim:
        facts.add(("target", asset_key, _norm(asset.target_verbatim)))
    for p in asset.programs:
        ind = _norm(p.indication_verbatim)
        facts.add(("program", asset_key, ind))
        facts.add(("phase", asset_key, ind, _norm_phase(p.phase_verbatim)))
    for partner in asset.partners:
        facts.add(("partner", asset_key, _norm(partner.name)))
    return facts


def _build_alias_to_canonical(result: ExtractionResult) -> dict[str, str]:
    """Map every alias of every gold asset to a single canonical key (its preferred_name)."""
    mapping: dict[str, str] = {}
    for a in result.assets:
        canonical = _norm(a.preferred_name)
        for k in _asset_keys(a):
            mapping[k] = canonical
    return mapping


def _facts(result: ExtractionResult, alias_to_canonical: dict[str, str]) -> set[tuple]:
    """Build the fact set, mapping each asset to its canonical key (gold's if matched)."""
    facts: set[tuple] = set()
    for a in result.assets:
        match = next((alias_to_canonical[k] for k in _asset_keys(a) if k in alias_to_canonical), None)
        asset_key = match or _norm(a.preferred_name)
        facts |= _facts_for_asset(asset_key, a)
    return facts


@dataclass
class CategoryScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 1.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class ScoreReport:
    overall: CategoryScore = field(default_factory=CategoryScore)
    by_category: dict[str, CategoryScore] = field(default_factory=dict)
    false_positives: list[tuple] = field(default_factory=list)
    false_negatives: list[tuple] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "overall": {"precision": round(self.overall.precision, 4),
                        "recall": round(self.overall.recall, 4),
                        "f1": round(self.overall.f1, 4),
                        "tp": self.overall.tp, "fp": self.overall.fp, "fn": self.overall.fn},
            "by_category": {
                c: {"precision": round(s.precision, 4), "recall": round(s.recall, 4),
                    "tp": s.tp, "fp": s.fp, "fn": s.fn}
                for c, s in sorted(self.by_category.items())
            },
        }


def score(predicted: ExtractionResult, gold: ExtractionResult) -> ScoreReport:
    alias = _build_alias_to_canonical(gold)
    pred_facts = _facts(predicted, alias)
    gold_facts = _facts(gold, alias)

    report = ScoreReport()
    for fact in pred_facts | gold_facts:
        category = fact[0]
        cs = report.by_category.setdefault(category, CategoryScore())
        in_pred, in_gold = fact in pred_facts, fact in gold_facts
        if in_pred and in_gold:
            cs.tp += 1
            report.overall.tp += 1
        elif in_pred:
            cs.fp += 1
            report.overall.fp += 1
            report.false_positives.append(fact)
        else:
            cs.fn += 1
            report.overall.fn += 1
            report.false_negatives.append(fact)
    return report
