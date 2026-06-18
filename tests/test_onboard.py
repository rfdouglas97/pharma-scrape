import pytest
from sqlalchemy import delete, select

from pipeline_intel.db import session
from pipeline_intel.gold.models import Company
from pipeline_intel.onboard import onboard_company

pytestmark = pytest.mark.usefixtures("require_db")

_TK = "ZZTEST"


def test_onboard_records_unresolved_attempt():
    """An unresolved company is still registered (status=unresolved) so the universe walk
    treats it as attempted and never retries it."""
    def resolve_fn():
        return {"pipeline_url": None, "method": None, "validated": False, "rationale": "unknown"}

    try:
        out = onboard_company("ZZ Test Pharma", _TK, run=False, resolve_fn=resolve_fn)
        assert out["status"] == "unresolved"
        with session() as s:
            c = s.execute(select(Company).where(Company.ticker == _TK)).scalar_one_or_none()
            assert c is not None and c.pipeline_status == "unresolved"
    finally:
        with session() as s:
            s.execute(delete(Company).where(Company.ticker == _TK))
