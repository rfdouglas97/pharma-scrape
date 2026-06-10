"""DB-backed tests for the thin silver->gold loader: SCD2 history, idempotency, and
exact-synonym asset dedup. Builds snapshot+extraction rows directly (no network)."""

import uuid

import pytest
from sqlalchemy import func, select

from pipeline_intel.db import session
from pipeline_intel.gold.models import (
    Asset,
    Company,
    CompanySource,
    Extraction,
    Program,
    ProgramVersion,
    Snapshot,
)
from pipeline_intel.gold.upsert import load_extraction
from pipeline_intel.registry.seed import seed_all

pytestmark = pytest.mark.usefixtures("require_db")


@pytest.fixture
def uniq() -> str:
    # DB-backed tests commit; unique names keep re-runs from colliding on scalar_one().
    return uuid.uuid4().hex[:8]


def _make_extraction(s, company_name, assets) -> str:
    company = Company(name=company_name)
    s.add(company)
    s.flush()
    src = CompanySource(company_id=company.company_id, url=f"https://x/{company_name}")
    s.add(src)
    s.flush()
    snap = Snapshot(source_id=src.source_id, content_hash="h" * 8, html_key="k")
    s.add(snap)
    s.flush()
    ext = Extraction(snapshot_id=snap.snapshot_id, raw_json={"assets": assets, "page_notes": None})
    s.add(ext)
    s.flush()
    return ext.extraction_id


def _asset(name, phase, indication="NSCLC", synonyms=None, status=None):
    return {
        "preferred_name": name, "synonyms": synonyms or [], "modality_verbatim": "mAb",
        "target_verbatim": None, "mechanism_verbatim": None, "originator_verbatim": None,
        "partners": [],
        "programs": [{"indication_verbatim": indication, "phase_verbatim": phase,
                      "status": status, "additional_fields": []}],
        "additional_fields": [],
    }


def test_load_creates_gold_and_is_idempotent(uniq):
    seed_all()
    company = f"TestCo-Load-{uniq}"
    with session() as s:
        eid = _make_extraction(s, company, [_asset(f"ABC-100-{uniq}", "Phase 2")])
        stats1 = load_extraction(s, eid)
        assert stats1.programs_new == 1
        assert stats1.versions_new == 1

        # Reload same extraction: no new program/version, just a touch.
        stats2 = load_extraction(s, eid)
        assert stats2.programs_new == 0
        assert stats2.versions_new == 0
        assert stats2.versions_touched == 1

        prog = s.execute(
            select(Program).join(Company, Company.company_id == Program.company_id)
            .where(Company.name == company)
        ).scalar_one()
        open_versions = s.execute(
            select(func.count()).select_from(ProgramVersion).where(
                ProgramVersion.program_id == prog.program_id, ProgramVersion.valid_to.is_(None)
            )
        ).scalar_one()
        assert open_versions == 1  # exactly one current version


def test_phase_change_creates_scd2_history(uniq):
    seed_all()
    with session() as s:
        company = f"TestCo-SCD2-{uniq}"
        asset = f"XYZ-1-{uniq}"
        e1 = _make_extraction(s, company, [_asset(asset, "Phase 1")])
        load_extraction(s, e1)

        # New snapshot for the SAME company/source, asset moved to Phase 2
        src = s.execute(
            select(CompanySource).join(Company, Company.company_id == CompanySource.company_id)
            .where(Company.name == company)
        ).scalar_one()
        snap2 = Snapshot(source_id=src.source_id, content_hash="h2" * 8, html_key="k2")
        s.add(snap2)
        s.flush()
        e2 = Extraction(snapshot_id=snap2.snapshot_id,
                        raw_json={"assets": [_asset(asset, "Phase 2")], "page_notes": None})
        s.add(e2)
        s.flush()
        stats = load_extraction(s, e2.extraction_id)
        assert stats.versions_changed == 1

        prog = s.execute(
            select(Program).join(Company, Company.company_id == Program.company_id)
            .where(Company.name == company)
        ).scalar_one()
        versions = s.execute(
            select(ProgramVersion).where(ProgramVersion.program_id == prog.program_id)
            .order_by(ProgramVersion.valid_from)
        ).scalars().all()
        assert len(versions) == 2  # closed P1 + open P2
        assert versions[0].valid_to is not None and versions[0].phase_code == "phase_1"
        assert versions[1].valid_to is None and versions[1].phase_code == "phase_2"


def test_synonym_dedupes_to_one_asset(uniq):
    seed_all()
    primary = f"ABC-200-{uniq}"
    shared = f"compound-Z-{uniq}"
    with session() as s:
        # Two companies, same asset under different name forms (partnered asset)
        e1 = _make_extraction(s, f"Partner-A-{uniq}", [_asset(primary, "Phase 2", synonyms=[shared])])
        load_extraction(s, e1)
        e2 = _make_extraction(s, f"Partner-B-{uniq}", [_asset(shared, "Phase 3")])
        load_extraction(s, e2)

        # Both extractions should resolve to the SAME asset via the shared synonym.
        a = s.execute(
            select(Asset).where(func.lower(Asset.preferred_name) == primary.lower())
        ).scalar_one()
        syns = {x.synonym.lower() for x in a.synonyms}
        assert shared.lower() in syns  # Partner-B's asset merged into Partner-A's
        # ...and no separate asset was created under the synonym name.
        dupes = s.execute(
            select(func.count()).select_from(Asset).where(
                func.lower(Asset.preferred_name) == shared.lower()
            )
        ).scalar_one()
        assert dupes == 0
