"""Eval-harness tests that don't touch the network (run_eval, which calls the model, is
exercised manually via `pipeline eval` once a labeled golden set + API key exist)."""

import json

from pipeline_intel.extract.schemas import ExtractionResult
from pipeline_intel.quality.eval_harness import load_fixtures


def test_load_fixtures_empty_dir_is_safe(tmp_path):
    assert load_fixtures(tmp_path) == []


def test_load_labeled_fixture(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps(
        {"company": "Acme", "url": "https://acme.example/pipeline",
         "format": "html_table", "labeled": True}))
    (d / "page.txt").write_text("ABC-123 | PD-1 | NSCLC | Phase 2")
    (d / "expected.json").write_text(json.dumps({
        "assets": [{
            "preferred_name": "ABC-123", "synonyms": [], "modality_verbatim": "mAb",
            "target_verbatim": "PD-1", "mechanism_verbatim": None, "originator_verbatim": None,
            "partners": [],
            "programs": [{"indication_verbatim": "NSCLC", "phase_verbatim": "Phase 2",
                          "status": None, "additional_fields": []}],
            "additional_fields": [],
        }],
        "page_notes": None,
    }))

    fixtures = load_fixtures(tmp_path)
    assert len(fixtures) == 1
    fx = fixtures[0]
    assert fx.company == "Acme"
    assert fx.fmt == "html_table"
    assert isinstance(fx.gold, ExtractionResult)
    assert fx.gold.assets[0].preferred_name == "ABC-123"
    assert fx.page_text.startswith("ABC-123")


def test_unlabeled_template_dir_is_loaded_but_inert(tmp_path):
    # A scaffold without expected.json is skipped (not a usable fixture yet).
    d = tmp_path / "draft"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"company": "Draft"}))
    (d / "page.txt").write_text("...")
    assert load_fixtures(tmp_path) == []


def test_model_seeded_draft_not_counted_until_labeled(tmp_path):
    # A model-seeded fixture (labeled != true) must NOT be scored — that would grade
    # the model against its own output.
    d = tmp_path / "seeded"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps(
        {"company": "Seeded", "labeled": False, "seeded_from_model": True}))
    (d / "expected.json").write_text(json.dumps({"assets": [], "page_notes": None}))
    assert load_fixtures(tmp_path) == []
