import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

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


@pytest.fixture
def tx(require_db):
    """A Session in a transaction that is ALWAYS rolled back — DB tests get full
    isolation and never pollute gold (no leftover TestCo/Partner rows)."""
    conn = engine().connect()
    trans = conn.begin()
    s = sessionmaker(bind=conn)()
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()
