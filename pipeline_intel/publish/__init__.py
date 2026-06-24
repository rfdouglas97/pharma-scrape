"""Publish layer: build + refresh the `published` contract schema the trading system reads.

- `init_published()` — (re)create the schema, views, matviews, role + grants. Idempotent
  (drops + recreates `published`, so it always reflects the current definitions).
- `refresh()` — REFRESH the materialized views (born_on, then edges). Run after each
  ingest/enrich cycle so the live read surface stays current; no weekly snapshot.

DDL uses the raw driver (exec_driver_sql) — no SQLAlchemy text() parsing of casts / $$ blocks.
"""

from __future__ import annotations

from pipeline_intel.db import engine
from pipeline_intel.publish import schema as S


def init_published(*, with_grants: bool = True) -> dict:
    """Drop + recreate the published schema from current definitions. Returns object counts."""
    eng = engine()
    with eng.begin() as conn:  # DDL is transactional in Postgres
        conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {S.SCHEMA} CASCADE")
        for stmt in S.create_sql():
            conn.exec_driver_sql(stmt)
        if with_grants:
            for stmt in S.grant_sql():
                conn.exec_driver_sql(stmt)
    return counts()


def refresh(*, concurrently: bool = True) -> dict:
    """Refresh the materialized views in dependency order (born_on before edges).

    CONCURRENTLY avoids read locks but cannot run in a transaction → autocommit connection.
    Falls back to a plain (locking) refresh if concurrent isn't possible.
    """
    eng = engine()
    mode = "CONCURRENTLY " if concurrently else ""
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for mv in S.MATVIEWS:
            try:
                conn.exec_driver_sql(f"REFRESH MATERIALIZED VIEW {mode}{S.SCHEMA}.{mv}")
            except Exception:
                # e.g. never-populated matview can't refresh concurrently — retry plain.
                conn.exec_driver_sql(f"REFRESH MATERIALIZED VIEW {S.SCHEMA}.{mv}")
    return counts()


def counts() -> dict:
    """Row counts for the published objects — used by `pipeline publish` output + verification."""
    eng = engine()
    objs = [v[0] for v in S._VIEWS] + S.MATVIEWS
    out: dict[str, int] = {}
    with eng.connect() as conn:
        for name in objs:
            out[name] = conn.exec_driver_sql(
                f"SELECT count(*) FROM {S.SCHEMA}.{name}"
            ).scalar_one()
    return out
