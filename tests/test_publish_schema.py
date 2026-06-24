"""Unit tests for the published-contract DDL builders — pure string/shape checks, no DB.

Guards the trading-DB contract (`docs/PUBLISHED_DB.md`): schema + view coverage, matview
dependency order, point-in-time columns, and least-privilege grants. No DB connection, so
these are safe to run while another process is using the shared database.
"""

from pipeline_intel.publish import schema as S


def test_create_sql_covers_schema_and_all_objects():
    sql = "\n".join(S.create_sql())
    assert f"CREATE SCHEMA IF NOT EXISTS {S.SCHEMA}" in sql
    for name, _ in S._VIEWS:  # every declared view is created in the published schema
        assert f"{S.SCHEMA}.{name}" in sql
    for mv in S.MATVIEWS:
        assert f"MATERIALIZED VIEW IF NOT EXISTS {S.SCHEMA}.{mv}" in sql


def test_born_on_matview_created_before_dependents():
    # program view and edge matview both reference program_born_on → it must be created first.
    stmts = S.create_sql()
    born = next(i for i, s in enumerate(stmts) if "program_born_on AS" in s)
    program = next(i for i, s in enumerate(stmts) if f"VIEW {S.SCHEMA}.program AS" in s)
    edge = next(i for i, s in enumerate(stmts) if "program_edge AS" in s)
    assert born < program
    assert born < edge


def test_matviews_in_refresh_dependency_order():
    # edges join born_on, so born_on must refresh first.
    assert S.MATVIEWS == ["program_born_on", "program_edge"]


def test_grants_are_least_privilege_published_only():
    grants = "\n".join(S.grant_sql())
    assert S.READER_ROLE in grants
    assert f"GRANT USAGE ON SCHEMA {S.SCHEMA}" in grants
    assert f"GRANT SELECT ON ALL TABLES IN SCHEMA {S.SCHEMA}" in grants
    # matviews need an explicit grant (not covered by ALL TABLES)
    assert f"{S.SCHEMA}.program_edge" in grants
    # nothing escapes the published schema to internal tables
    assert "public." not in grants


def test_contract_columns_present():
    sql = "\n".join(S.create_sql())
    # point-in-time backbone + the join key the trading side depends on
    for col in ("born_on", "ticker", "link_type", "valid_from", "valid_to"):
        assert col in sql
    # born_on is anchored on the first-seen snapshot
    assert "first_seen_snapshot_id" in sql
