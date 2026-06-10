import pytest

from pipeline_intel.extract.schemas import (
    ExtractedAsset,
    ExtractedProgram,
    ExtractionResult,
)
from pipeline_intel.quality.scorer import score


def _asset(name, synonyms=None, modality=None, target=None, programs=None, partners=None):
    return ExtractedAsset(
        preferred_name=name,
        synonyms=synonyms or [],
        modality_verbatim=modality,
        target_verbatim=target,
        programs=programs or [],
    )


def _prog(ind, phase, status=None):
    return ExtractedProgram(indication_verbatim=ind, phase_verbatim=phase, status=status)


def test_perfect_match_scores_one():
    gold = ExtractionResult(assets=[
        _asset("ABC-123", modality="mAb", target="PD-1",
               programs=[_prog("NSCLC", "Phase 2"), _prog("melanoma", "Phase 1")]),
    ])
    report = score(gold, gold)
    assert report.overall.precision == 1.0
    assert report.overall.recall == 1.0


def test_phase_synonyms_count_as_match():
    gold = ExtractionResult(assets=[_asset("X", programs=[_prog("NSCLC", "Phase 2")])])
    pred = ExtractionResult(assets=[_asset("X", programs=[_prog("NSCLC", "Ph2")])])
    report = score(pred, gold)
    assert report.by_category["phase"].tp == 1
    assert report.overall.recall == 1.0


@pytest.mark.parametrize("gold_phase,pred_phase", [
    ("Phase 2", "Phase 2 in Progress"),   # BMS-style suffix
    ("Phase 3", "Phase III"),             # roman vs arabic
    ("Registration", "Registration (EU, JP)"),  # parenthetical qualifier
    ("Filed", "Registration"),            # alias
    ("Approved", "Regulatory Approval Achieved"),
    ("Phase 3", "Phase III *"),           # footnote marker
])
def test_phase_normalization_matches_verbatim_variants(gold_phase, pred_phase):
    gold = ExtractionResult(assets=[_asset("X", programs=[_prog("NSCLC", gold_phase)])])
    pred = ExtractionResult(assets=[_asset("X", programs=[_prog("NSCLC", pred_phase)])])
    report = score(pred, gold)
    assert report.by_category["phase"].tp == 1, f"{pred_phase!r} should match {gold_phase!r}"
    assert report.by_category["phase"].fp == 0 and report.by_category["phase"].fn == 0


def test_synonym_matches_asset_identity():
    gold = ExtractionResult(assets=[_asset("ABC-123", synonyms=["compound X"], target="PD-1")])
    # model used the synonym as the preferred name — should still match the same asset
    pred = ExtractionResult(assets=[_asset("compound X", target="PD-1")])
    report = score(pred, gold)
    assert report.by_category["target"].tp == 1
    assert report.by_category["target"].fn == 0


def test_missed_asset_is_false_negative():
    gold = ExtractionResult(assets=[
        _asset("A", programs=[_prog("NSCLC", "Phase 2")]),
        _asset("B", programs=[_prog("AML", "Phase 1")]),
    ])
    pred = ExtractionResult(assets=[_asset("A", programs=[_prog("NSCLC", "Phase 2")])])
    report = score(pred, gold)
    assert report.overall.recall < 1.0
    assert report.overall.precision == 1.0  # nothing wrong predicted, just incomplete
    assert ("asset", "b") in report.false_negatives


def test_wrong_phase_is_both_fp_and_fn():
    gold = ExtractionResult(assets=[_asset("A", programs=[_prog("NSCLC", "Phase 2")])])
    pred = ExtractionResult(assets=[_asset("A", programs=[_prog("NSCLC", "Phase 3")])])
    report = score(pred, gold)
    ph = report.by_category["phase"]
    assert ph.tp == 0 and ph.fp == 1 and ph.fn == 1


def test_hallucinated_target_is_false_positive():
    gold = ExtractionResult(assets=[_asset("A", programs=[_prog("NSCLC", "Phase 2")])])
    pred = ExtractionResult(assets=[_asset("A", target="EGFR", programs=[_prog("NSCLC", "Phase 2")])])
    report = score(pred, gold)
    assert report.by_category["target"].fp == 1
    assert report.overall.precision < 1.0
