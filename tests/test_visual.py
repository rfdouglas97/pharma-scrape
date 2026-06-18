import json
from pathlib import Path

from pipeline_intel.extract.schemas import VisualEvidenceRow, VisualTranscription
from pipeline_intel.extract.visual import VISUAL_MODEL, normalize_transcription, resolve_visual_model

FIXTURES = Path(__file__).parent / "fixtures"


def _row(name, indication, target, phase, modality=None, status=None, conf=0.9):
    return VisualEvidenceRow(
        asset_name=name, indication=indication, target=target, modality=modality,
        phase=phase, status=status, phase_evidence="bar ends in column", confidence=conf,
    )


def test_normalize_maps_rows_to_assets_and_programs():
    t = VisualTranscription(
        phase_columns=["Preclinical", "Phase 1/2", "Registrational", "Commercial"],
        rows=[
            _row("KB407", "Cystic fibrosis", "CFTR", "Phase 1/2"),
            _row("KB801", "Neurotrophic keratitis", "NGF", "Registrational"),
        ],
    )
    res = normalize_transcription(t)
    assert [a.preferred_name for a in res.assets] == ["KB407", "KB801"]
    kb407 = res.assets[0]
    assert kb407.target_verbatim == "CFTR"
    assert kb407.programs[0].indication_verbatim == "Cystic fibrosis"
    assert kb407.programs[0].phase_verbatim == "Phase 1/2"
    assert "chart image" in (res.page_notes or "")


def test_normalize_collapses_same_asset_into_multiple_programs():
    t = VisualTranscription(
        phase_columns=["Preclinical", "Phase 1/2"],
        rows=[
            _row("KB707", "NSCLC", "IL2 + IL12", "Phase 1/2"),
            _row("KB707", "Solid tumors", None, "Phase 1/2"),
        ],
    )
    res = normalize_transcription(t)
    assert len(res.assets) == 1
    asset = res.assets[0]
    assert len(asset.programs) == 2
    # target fills from the first row that disclosed it
    assert asset.target_verbatim == "IL2 + IL12"


def test_normalize_uses_asset_name_when_indication_missing():
    t = VisualTranscription(
        phase_columns=["Preclinical"],
        rows=[_row("Additional respiratory programs", None, None, "Preclinical")],
    )
    res = normalize_transcription(t)
    assert res.assets[0].programs[0].indication_verbatim == "Additional respiratory programs"


def test_resolve_visual_model_defaults_to_opus():
    assert resolve_visual_model(None) == VISUAL_MODEL
    assert resolve_visual_model("claude-sonnet-4-6") == VISUAL_MODEL  # default -> opus
    assert resolve_visual_model("claude-haiku-4-5") == "claude-haiku-4-5"  # explicit kept


def test_krystal_transcription_fixture_normalizes_to_11_programs_with_targets():
    """Regression on a recorded real Krystal chart transcription: the normalization contract
    must yield 11 programs and preserve the Payload->target mapping the generic path missed."""
    data = json.loads((FIXTURES / "krystal_transcription.json").read_text())
    transcription = VisualTranscription.model_validate(data)
    res = normalize_transcription(transcription)

    assert len(res.assets) == 11
    assert sum(len(a.programs) for a in res.assets) == 11

    by_name = {a.preferred_name: a for a in res.assets}
    assert by_name["KB407"].target_verbatim == "CFTR"
    assert by_name["KB801"].target_verbatim == "NGF"
    assert by_name["Inhaled KB707"].target_verbatim == "IL2 + IL12"
    assert by_name["Vyjuvek (beremagene geperpavec-svdt)"].programs[0].phase_verbatim == "Commercial"
    # 8 named molecules disclose a target; the 3 grouped aggregate rows legitimately do not
    assert sum(1 for a in res.assets if a.target_verbatim) == 8
