"""DB-backed tests: seed idempotency, and the hash-skip behavior in write_snapshot.

Uses a fake RenderResult so there is no network dependency — only Postgres + storage.
"""

import pytest
from sqlalchemy import func, select

from pipeline_intel.db import session
from pipeline_intel.gold.models import Company, CompanySource, PhaseVocab
from pipeline_intel.ingest.render import RenderResult
from pipeline_intel.ingest.snapshot import write_snapshot
from pipeline_intel.ingest.storage import LocalStorage
from pipeline_intel.registry.seed import seed_all

pytestmark = pytest.mark.usefixtures("require_db")


def test_seed_is_idempotent():
    first = seed_all()
    with session() as s:
        companies_after_first = s.execute(select(func.count(Company.company_id))).scalar_one()
    second = seed_all()
    with session() as s:
        companies_after_second = s.execute(select(func.count(Company.company_id))).scalar_one()
    assert companies_after_first == companies_after_second
    assert first["companies"] == second["companies"]
    with session() as s:
        assert s.get(PhaseVocab, "phase_2") is not None


def test_hash_skip(tx, tmp_path):
    # `tx` is rolled back, so the snapshots written here never pollute the dev DB.
    seed_all()
    store = LocalStorage(str(tmp_path))
    src = tx.execute(select(CompanySource).limit(1)).scalar_one()
    source_id = src.source_id

    rr = RenderResult(
        url=src.url, http_status=200,
        html="<html>Phase 2 NSCLC</html>", text="Phase 2 NSCLC",
        screenshot=b"\x89PNG-fake", meta={},
    )
    snap1, changed1 = write_snapshot(tx, store, source_id, "testco", rr)
    assert changed1 is True
    assert snap1.html_key is not None  # artifacts written on change

    # Identical content -> unchanged, no artifacts
    snap2, changed2 = write_snapshot(tx, store, source_id, "testco", rr)
    assert changed2 is False
    assert snap2.html_key is None

    # Different content -> changed again
    rr.text = "Phase 3 NSCLC"
    snap3, changed3 = write_snapshot(tx, store, source_id, "testco", rr)
    assert changed3 is True
    assert snap3.html_key is not None
