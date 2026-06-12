"""DB-backed test for the longitudinal change-feed rebuild (silver -> change_event).

Builds snapshot+extraction rows with captured_at dates, loads them to gold (so assets exist),
then rebuilds the feed and asserts the add / phase-advance / confirmed-discontinuation events.
Uses the rolled-back `tx` fixture; skips when no DB is reachable.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from pipeline_intel.gold.models import ChangeEvent, Company, CompanySource, Extraction, Snapshot
from pipeline_intel.gold.upsert import load_extraction
from pipeline_intel.history.rebuild import rebuild_history
from pipeline_intel.registry.seed import seed_all


@pytest.fixture
def uniq() -> str:
    return uuid.uuid4().hex[:8]


def _asset(name, phase, indication="NSCLC"):
    return {
        "preferred_name": name, "synonyms": [], "modality_verbatim": None,
        "target_verbatim": None, "mechanism_verbatim": None, "originator_verbatim": None,
        "partners": [],
        "programs": [{"indication_verbatim": indication, "phase_verbatim": phase,
                      "status": None, "additional_fields": []}],
        "additional_fields": [],
    }


def _add_capture(s, src_id, when, assets):
    snap = Snapshot(source_id=src_id, content_hash=uuid.uuid4().hex, captured_at=when, origin="wayback")
    s.add(snap)
    s.flush()
    ext = Extraction(snapshot_id=snap.snapshot_id, raw_json={"assets": assets, "page_notes": None})
    s.add(ext)
    s.flush()
    load_extraction(s, ext.extraction_id)


def _by_type(rows):
    out = {}
    for r in rows:
        out.setdefault(r.event_type, []).append(r)
    return out


def test_rebuild_history_add_advance_discontinue(tx, uniq):
    seed_all()  # vocab for phase normalization (committed, idempotent)
    co = Company(name=f"HistCo-{uniq}")
    tx.add(co)
    tx.flush()
    src = CompanySource(company_id=co.company_id, url=f"https://x/{uniq}")
    tx.add(src)
    tx.flush()
    a, b = f"DrugA-{uniq}", f"DrugB-{uniq}"
    _add_capture(tx, src.source_id, datetime(2021, 1, 1, tzinfo=UTC), [_asset(a, "Phase 1")])
    _add_capture(tx, src.source_id, datetime(2021, 4, 1, tzinfo=UTC),
                 [_asset(a, "Phase 2"), _asset(b, "Phase 1")])
    _add_capture(tx, src.source_id, datetime(2021, 7, 1, tzinfo=UTC), [_asset(a, "Phase 2")])
    _add_capture(tx, src.source_id, datetime(2021, 10, 1, tzinfo=UTC), [_asset(a, "Phase 2")])

    stats = rebuild_history(tx, co.company_id)
    rows = tx.execute(select(ChangeEvent).where(ChangeEvent.company_id == co.company_id)).scalars().all()
    by = _by_type(rows)

    assert stats["captures"] == 4
    assert any(r.direction == "advance" and r.from_phase == "Phase 1" and r.to_phase == "Phase 2"
               for r in by.get("asset_phase_changed", []))
    assert len(by.get("asset_added", [])) == 1            # DrugB entered at cap2
    left = by.get("asset_left_pipeline", [])
    assert len(left) == 1                                  # DrugB confirmed gone (absent cap3 & cap4)
    assert left[0].status == "confirmed"
    assert left[0].exit_class == "likely_discontinued_early"


def test_rebuild_is_idempotent(tx, uniq):
    seed_all()
    co = Company(name=f"HistCo2-{uniq}")
    tx.add(co)
    tx.flush()
    src = CompanySource(company_id=co.company_id, url=f"https://x2/{uniq}")
    tx.add(src)
    tx.flush()
    a = f"DrugA-{uniq}"
    _add_capture(tx, src.source_id, datetime(2021, 1, 1, tzinfo=UTC), [_asset(a, "Phase 1")])
    _add_capture(tx, src.source_id, datetime(2021, 4, 1, tzinfo=UTC), [_asset(a, "Phase 2")])

    def count():
        return tx.execute(
            select(func.count()).select_from(ChangeEvent).where(ChangeEvent.company_id == co.company_id)
        ).scalar()

    s1 = rebuild_history(tx, co.company_id)
    n1 = count()
    rebuild_history(tx, co.company_id)
    n2 = count()
    assert n1 == n2 == s1["events"]  # rewrite, not append — no duplication
