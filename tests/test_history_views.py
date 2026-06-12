"""Accuracy tests for the history views — these encode the non-negotiable guardrails:
exits are split by class (a discontinuation count must NOT include approvals/late exits), bad-capture
quarters are flagged (never plotted as real composition), and TA/partner normalization is correct.
"""

import uuid
from datetime import UTC, datetime

import pytest

from pipeline_intel.gold.models import Company, CompanySource, Extraction, Snapshot
from pipeline_intel.gold.upsert import load_extraction
from pipeline_intel.history.distribution import history_summary, period_distribution
from pipeline_intel.history.rebuild import rebuild_history
from pipeline_intel.normalize.partner import normalize_partner
from pipeline_intel.normalize.therapeutic_area import disease_area_to_ta
from pipeline_intel.registry.seed import seed_all


# --- pure unit tests (no DB) ---
def test_therapeutic_area_mapping():
    assert disease_area_to_ta("Solid Tumors", "Melanoma") == "Oncology"
    assert disease_area_to_ta("Lymphoma", None) == "Oncology"          # blood cancer -> Oncology
    assert disease_area_to_ta("Immunology", "Ulcerative Colitis") == "Immunology & Inflammation"
    assert disease_area_to_ta("Neuroscience", "Alzheimer's Disease") == "Neuroscience"
    assert disease_area_to_ta("Beta-Thalassemia", None) == "Hematology (non-malignant)"
    assert disease_area_to_ta(None, None) == "Other / Uncategorized"


def test_partner_normalization_collapses_formatting_keeps_real_handoffs():
    assert normalize_partner("Janssen Pharmaceuticals, Inc.") == "Johnson & Johnson"
    assert normalize_partner("Halozyme Therapeutics") == normalize_partner("Halozyme")
    # a real licensing handoff must NOT collapse to the same entity
    assert normalize_partner("Acceleron Pharma") != normalize_partner("Merck")


# --- DB tests (tx; skipped without a database) ---
@pytest.fixture
def uniq() -> str:
    return uuid.uuid4().hex[:8]


def _asset(name, phase, indication, area, partners=None):
    return {
        "preferred_name": name, "synonyms": [], "modality_verbatim": None, "target_verbatim": None,
        "mechanism_verbatim": None, "originator_verbatim": None,
        "partners": [{"name": p, "role": None, "territory": None} for p in (partners or [])],
        "programs": [{"indication_verbatim": indication, "phase_verbatim": phase, "status": None,
                      "additional_fields": [{"name": "Disease Area", "value": area}]}],
        "additional_fields": [],
    }


def _cap(s, src_id, when, quarter, assets):
    snap = Snapshot(source_id=src_id, content_hash=uuid.uuid4().hex, captured_at=when,
                    origin="wayback", render_meta={"quarter": quarter})
    s.add(snap)
    s.flush()
    ext = Extraction(snapshot_id=snap.snapshot_id, raw_json={"assets": assets, "page_notes": None})
    s.add(ext)
    s.flush()
    load_extraction(s, ext.extraction_id)


def test_exit_split_quarantine_and_ta(tx, uniq):
    seed_all()
    co = Company(name=f"HistViz-{uniq}")
    tx.add(co)
    tx.flush()
    src = CompanySource(company_id=co.company_id, url=f"https://x/{uniq}")
    tx.add(src)
    tx.flush()

    def bg():  # stable immunology background so single removals don't trip the bad-capture guard
        return [_asset(f"BG{i}-{uniq}", "Phase 2", "Ulcerative Colitis", "Immunology") for i in range(8)]

    late = _asset(f"Late-{uniq}", "Phase 3", "Melanoma", "Oncology")     # leaves from Ph3 -> ambiguous
    early = _asset(f"Early-{uniq}", "Phase 1", "Glioblastoma", "Oncology")  # leaves from Ph1 -> discontinued
    d = datetime
    _cap(tx, src.source_id, d(2021, 1, 1, tzinfo=UTC), "2021Q1", bg() + [late, early])
    _cap(tx, src.source_id, d(2021, 4, 1, tzinfo=UTC), "2021Q2", bg())             # both gone (absent 1)
    _cap(tx, src.source_id, d(2021, 7, 1, tzinfo=UTC), "2021Q3", bg())             # absent 2 -> confirmed
    _cap(tx, src.source_id, d(2021, 10, 1, tzinfo=UTC), "2021Q4", [bg()[0]])       # collapse -> quarantine

    rebuild_history(tx, co.company_id, origins=("wayback",))

    # Exit split: the Phase-1 exit is a confirmed discontinuation; the Phase-3 exit is NOT.
    summ = history_summary(tx, co.company_id)
    assert summ["totals"]["discontinued_confirmed"] == 1          # only `early`
    assert summ["totals"]["exits_approved_or_late"] == 1          # `late`, never folded in

    # TA distribution: 2021Q1 has 2 oncology programs + 8 immunology; unit is programs.
    ta = period_distribution(tx, co.company_id, "therapeutic_area")
    assert ta["unit"] == "programs"
    q1 = next(q for q in ta["quarters"] if q["period"] == "2021Q1")
    assert q1["counts"]["Oncology"] == 2
    assert q1["counts"]["Immunology & Inflammation"] == 8

    # Bad-capture guard: 2021Q4 collapsed and is flagged, not treated as a real composition.
    q4 = next(q for q in ta["quarters"] if q["period"] == "2021Q4")
    assert q4["quarantined"] is True
    assert all(not q["quarantined"] for q in ta["quarters"] if q["period"] != "2021Q4")


def test_rebuild_history_is_idempotent_for_views(tx, uniq):
    seed_all()
    co = Company(name=f"HistViz2-{uniq}")
    tx.add(co)
    tx.flush()
    src = CompanySource(company_id=co.company_id, url=f"https://x2/{uniq}")
    tx.add(src)
    tx.flush()
    d = datetime
    a1 = _asset(f"A-{uniq}", "Phase 1", "NSCLC", "Oncology")
    a2 = _asset(f"A-{uniq}", "Phase 2", "NSCLC", "Oncology")
    _cap(tx, src.source_id, d(2021, 1, 1, tzinfo=UTC), "2021Q1", [a1])
    _cap(tx, src.source_id, d(2021, 4, 1, tzinfo=UTC), "2021Q2", [a2])
    a = rebuild_history(tx, co.company_id, origins=("wayback",))
    b = rebuild_history(tx, co.company_id, origins=("wayback",))
    assert a["by_type"] == b["by_type"]
