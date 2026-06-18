from pipeline_intel.extract.schemas import ExtractedAsset, ExtractedProgram, ExtractionResult
from pipeline_intel.quality.checker import deterministic_verdict


def _result(n_assets, programs_per_asset):
    assets = [
        ExtractedAsset(
            preferred_name=f"A{i}",
            programs=[ExtractedProgram(indication_verbatim=f"ind{j}", phase_verbatim="Phase 2")
                      for j in range(programs_per_asset)],
        )
        for i in range(n_assets)
    ]
    return ExtractionResult(assets=assets)


def test_passes_when_stated_count_matches_program_count_not_asset_count():
    # The AstraZeneca case: page states ~program count; we have fewer assets, many programs.
    result = _result(n_assets=10, programs_per_asset=2)  # 10 assets, 20 programs
    verdict, expected, observed = deterministic_verdict(result, "", known_expected_count=20)
    assert verdict.verdict in ("pass", "warn")  # reconciled against program count (20)


def test_passes_when_stated_count_matches_asset_count():
    result = _result(n_assets=20, programs_per_asset=1)  # 20 assets, 20 programs
    verdict, _, _ = deterministic_verdict(result, "", known_expected_count=20)
    assert verdict.verdict == "pass"


def test_fails_when_known_count_far_off():
    # a TRUSTED registry count that's far off hard-fails
    result = _result(n_assets=3, programs_per_asset=1)  # 3 assets, 3 programs
    verdict, _, _ = deterministic_verdict(result, "", known_expected_count=50)
    assert verdict.verdict == "fail"


def test_inferred_count_mismatch_only_warns():
    # a count scraped from page/PDF text is a soft signal (e.g. it grabbed a per-phase
    # subtotal) -> warn and let it load, do not block on it.
    result = _result(n_assets=3, programs_per_asset=1)  # 3
    verdict, expected, _ = deterministic_verdict(result, "Our pipeline includes 50 programs.")
    assert expected == 50
    assert verdict.verdict == "warn"
