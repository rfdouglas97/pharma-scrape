import pytest
from sqlalchemy import text

from pipeline_intel.db import engine


@pytest.fixture(scope="session")
def db_available() -> bool:
    try:
        with engine().connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


@pytest.fixture
def require_db(db_available):
    if not db_available:
        pytest.skip("no database reachable (set DATABASE_URL / start docker compose)")
