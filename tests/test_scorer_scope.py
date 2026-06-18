from pipeline_intel.extract.schemas import ExtractedAsset, ExtractedProgram, ExtractionResult
from pipeline_intel.quality.scorer import score


def _asset(name, indication="NSCLC", phase="Phase 2", target=None):
    return ExtractedAsset(
        preferred_name=name, target_verbatim=target,
        programs=[ExtractedProgram(indication_verbatim=indication, phase_verbatim=phase)],
    )


def test_scope_to_gold_categories_excludes_unlabeled_dims():
    # gold (from an xlsx) has no target column; prediction (correctly) adds a target.
    gold = ExtractionResult(assets=[_asset("ABC")])
    pred = ExtractionResult(assets=[_asset("ABC", target="EGFR")])

    unscoped = score(pred, gold)
    assert "target" in unscoped.by_category  # counted as a false positive
    assert unscoped.overall.precision < 1.0

    scoped = score(pred, gold, scope_to_gold_categories=True)
    assert "target" not in scoped.by_category  # gold never labeled targets -> not evaluated
    assert scoped.overall.precision == 1.0
    assert scoped.overall.recall == 1.0


def test_scoping_still_penalizes_missing_covered_facts():
    # recall is still enforced on covered dimensions: a missed program is an fn.
    gold = ExtractionResult(assets=[_asset("ABC"), _asset("XYZ", indication="Melanoma")])
    pred = ExtractionResult(assets=[_asset("ABC")])
    scoped = score(pred, gold, scope_to_gold_categories=True)
    assert scoped.overall.recall < 1.0


def _removed_asset(name, indication="OldInd"):
    return ExtractedAsset(
        preferred_name=name,
        programs=[ExtractedProgram(indication_verbatim=indication, phase_verbatim="Phase 1",
                                   status="Removed")],
    )


def test_active_only_excludes_removed_section():
    # gold (active-only xlsx) has one active asset; prediction also captured a Removed-section
    # asset (real page content). active_only must not count it as a false positive.
    gold = ExtractionResult(assets=[_asset("ABC")])
    pred = ExtractionResult(assets=[_asset("ABC"), _removed_asset("OLD-1")])

    assert score(pred, gold).overall.precision < 1.0  # removed item counts without the flag
    scoped = score(pred, gold, active_only=True)
    assert scoped.overall.precision == 1.0
    assert scoped.overall.recall == 1.0
