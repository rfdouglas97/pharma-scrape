from pipeline_intel.extract.schemas import ExtractedAsset, ExtractedProgram, ExtractionResult
from pipeline_intel.quality.checker import (
    CountMismatch,
    QAVerdict,
    deterministic_verdict,
    infer_expected_count,
    merge_verdicts,
)


def _result(n: int) -> ExtractionResult:
    return ExtractionResult(
        assets=[
            ExtractedAsset(
                preferred_name=f"ABC-{i}",
                programs=[
                    ExtractedProgram(
                        indication_verbatim="NSCLC",
                        phase_verbatim="Phase 2",
                    )
                ],
            )
            for i in range(n)
        ]
    )


def test_infer_expected_count_from_pipeline_text():
    assert infer_expected_count("Our pipeline includes 57 assets across phases.") == 57


def test_deterministic_verdict_passes_close_count():
    verdict, expected, observed = deterministic_verdict(
        _result(56),
        "Our pipeline includes 57 assets across phases.",
    )
    assert verdict.verdict == "warn"
    assert expected == 57
    assert observed == 56


def test_deterministic_verdict_fails_large_count_mismatch():
    # Only a TRUSTED registry count hard-fails on a large mismatch; inferred page counts warn.
    verdict, expected, observed = deterministic_verdict(
        _result(40),
        "Our pipeline includes 57 assets across phases.",
        known_expected_count=57,
    )
    assert verdict.verdict == "fail"
    assert expected == 57
    assert observed == 40
    assert verdict.recommended_action == "focused_reextract_missing_sections"


def test_deterministic_verdict_fails_large_prior_drop():
    verdict, expected, observed = deterministic_verdict(
        _result(20),
        "Pipeline overview",
        previous_observed_count=50,
    )
    assert verdict.verdict == "fail"
    assert expected is None
    assert observed == 20
    assert verdict.recommended_action == "rerender_with_repair_config"


def test_merge_verdicts_keeps_stricter_result():
    preflight = QAVerdict(
        verdict="fail",
        confidence=0.95,
        count_mismatches=[
            CountMismatch(label="asset_count", expected=57, observed=40, detail="bad count")
        ],
    )
    judge = QAVerdict(verdict="pass", confidence=0.8)
    merged = merge_verdicts(preflight, judge)
    assert merged.verdict == "fail"
    assert merged.confidence == 0.95
    assert len(merged.count_mismatches) == 1
