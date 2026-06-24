"""DDL for the `published` schema — the stable, spec-shaped contract surface that the
Project Rand trading system reads over a read-only Postgres connection.

Why a separate schema of views (not raw gold tables): it decouples the consumer from our
internal medallion schema (ULIDs, SCD2 mechanics, the asset×indication×company "program"
shape). The shapes here mirror `robinhood/design/pipeline_db_spec.md` so the trading side's
`PipelineDBGraph` wires up with no engine changes.

Why DDL here (not an Alembic migration): the published layer is a *derived, regenerable*
artifact — rebuilt/refreshed by `pipeline publish`, idempotent (CREATE OR REPLACE / IF NOT
EXISTS). That keeps it independent of migration state.

Mapping notes (gold → spec):
- spec `program` (a drug/asset node) == our **asset**, rolled up across its programs:
  primary company, most-advanced non-discontinued current phase, born_on.
- per-indication phase/status + SCD2 validity live in `program_indication` (point-in-time).
- derived `program_edge` (mechanism | competitive) is materialized + born_on-stamped.
"""

from __future__ import annotations

SCHEMA = "published"

# Read-only login the trading repo uses. Localhost dev default mirrors the pipeline:pipeline
# convention; set a real password (ALTER ROLE rand_reader PASSWORD '…') before exposing off-box.
READER_ROLE = "rand_reader"
READER_PASSWORD = "rand_reader"

# Phases at/above this sort_order are terminal (discontinued), not "more advanced" — excluded
# from the most-advanced-phase rollup.
DISCONTINUED_SORT = 70


# --- Views (live; CREATE OR REPLACE = idempotent) ---------------------------
# NB: program & program_edge reference the program_born_on matview, so the matviews are
# created first in init_published().

_VIEWS: list[tuple[str, str]] = [
    # spec.program — one row per asset (drug), rolled up to the company that owns it.
    (
        "program",
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.program AS
        SELECT
            a.asset_id                                   AS program_id,
            pc.company_id                                AS company_id,
            co.ticker                                    AS ticker,
            NULL::text                                   AS cik,   -- not tracked yet
            a.preferred_name                             AS drug_name,
            a.modality_code                              AS modality,
            a.modality_verbatim                          AS modality_verbatim,
            a.mechanism_verbatim                         AS moa,
            -- most-advanced current phase across this asset's programs, excluding discontinued
            (SELECT pv2.phase_code
               FROM program p2
               JOIN program_version pv2 ON pv2.program_id = p2.program_id AND pv2.valid_to IS NULL
               LEFT JOIN phase_vocab v2 ON v2.code = pv2.phase_code
              WHERE p2.asset_id = a.asset_id
                AND COALESCE(v2.sort_order, 0) < {DISCONTINUED_SORT}
              ORDER BY COALESCE(v2.sort_order, 0) DESC
              LIMIT 1)                                   AS phase,
            (SELECT CASE WHEN bool_or(pv3.status = 'active') THEN 'active' ELSE 'inactive' END
               FROM program p3
               JOIN program_version pv3 ON pv3.program_id = p3.program_id AND pv3.valid_to IS NULL
              WHERE p3.asset_id = a.asset_id)            AS status,
            NULL::boolean                                AS is_lead,  -- not tracked yet
            bo.born_on                                   AS born_on,
            bo.born_on_source                            AS born_on_source,
            a.chembl_id                                  AS chembl_id
        FROM asset a
        LEFT JOIN LATERAL (
            SELECT COALESCE(a.originator_company_id, g.company_id) AS company_id
            FROM (
                SELECT p.company_id
                FROM program p
                WHERE p.asset_id = a.asset_id
                GROUP BY p.company_id
                ORDER BY count(*) DESC, p.company_id
                LIMIT 1
            ) g
        ) pc ON true
        LEFT JOIN company co ON co.company_id = pc.company_id
        LEFT JOIN {SCHEMA}.program_born_on bo ON bo.program_id = a.asset_id
        """,
    ),
    # spec.program_indication — many-to-many, SCD2-bearing for point-in-time (as_of) reads.
    (
        "program_indication",
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.program_indication AS
        SELECT
            p.asset_id            AS program_id,
            p.indication_id       AS indication_id,
            p.company_id          AS company_id,
            pv.phase_code         AS phase,
            pv.status             AS status,
            pv.indication_verbatim AS indication_verbatim,
            pv.valid_from         AS valid_from,
            pv.valid_to           AS valid_to,
            NULL::boolean         AS is_lead_indication  -- not tracked yet
        FROM program p
        JOIN program_version pv ON pv.program_id = p.program_id
        """,
    ),
    # spec.program_target
    (
        "program_target",
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.program_target AS
        SELECT DISTINCT
            at.asset_id   AS program_id,
            at.target_id  AS target_id,
            at.action     AS role,
            at.source     AS source
        FROM asset_target at
        """,
    ),
    # spec.program_moa (free-text)
    (
        "program_moa",
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.program_moa AS
        SELECT asset_id AS program_id, mechanism_verbatim AS moa
        FROM asset
        WHERE mechanism_verbatim IS NOT NULL
        """,
    ),
    # spec.program_company — many-to-many sponsor/partner; join key to the company DB is ticker.
    (
        "program_company",
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.program_company AS
        SELECT DISTINCT p.asset_id AS program_id, p.company_id, c.ticker, 'sponsor'::text AS role
        FROM program p
        JOIN company c ON c.company_id = p.company_id
        UNION
        SELECT DISTINCT
            COALESCE(pg.asset_id, pr.asset_id) AS program_id,
            pr.partner_company_id              AS company_id,
            c.ticker,
            COALESCE(pr.role, 'partner')       AS role
        FROM partnership pr
        LEFT JOIN program pg ON pg.program_id = pr.program_id
        JOIN company c ON c.company_id = pr.partner_company_id
        WHERE pr.partner_company_id IS NOT NULL
          AND COALESCE(pg.asset_id, pr.asset_id) IS NOT NULL
        """,
    ),
    # spec.target
    (
        "target",
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.target AS
        SELECT target_id, hgnc_symbol, uniprot_id, ensembl_id, name
        FROM target
        """,
    ),
    # spec.indication — best ontology mapping (prefer MONDO, highest confidence) + parent rollup.
    (
        "indication",
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.indication AS
        SELECT
            i.indication_id,
            i.preferred_label AS name,
            m.curie           AS mondo_id,
            m.label           AS ontology_label,
            m.therapeutic_area,
            (SELECT array_agg(oc.ancestor_curie)
               FROM ontology_closure oc
              WHERE oc.descendant_curie = m.curie)  AS parent_ids
        FROM indication i
        LEFT JOIN LATERAL (
            SELECT im.curie, im.label, im.therapeutic_area
            FROM indication_mapping im
            WHERE im.indication_id = i.indication_id
              AND im.status IN ('auto', 'reviewed')
              AND im.curie IS NOT NULL
            ORDER BY (im.ontology = 'MONDO') DESC, im.confidence DESC NULLS LAST
            LIMIT 1
        ) m ON true
        """,
    ),
    # spec.drug_alias — for event→program entity resolution (brand / generic / code names).
    (
        "drug_alias",
        f"""
        CREATE OR REPLACE VIEW {SCHEMA}.drug_alias AS
        SELECT s.asset_id AS program_id, s.synonym AS alias, s.synonym_type AS alias_type
        FROM asset_synonym s
        UNION
        SELECT asset_id, preferred_name, 'preferred' FROM asset
        UNION
        SELECT asset_id, alias, 'durable' FROM asset_alias
        """,
    ),
]


# --- Materialized views (refreshed by `pipeline publish`) -------------------
# born_on: public-disclosure-anchored = earliest snapshot we first saw the program in
# (extended backward by the Wayback backfill, which sets snapshot.captured_at to the
# historical capture date). This is the point-in-time backbone for the backtest.
_MATVIEW_BORN_ON = f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS {SCHEMA}.program_born_on AS
SELECT
    a.asset_id AS program_id,
    min(COALESCE(s.captured_at, s.fetched_at)) AS born_on,
    'disclosure'::text AS born_on_source
FROM asset a
JOIN program p          ON p.asset_id = a.asset_id
JOIN program_version pv ON pv.program_id = p.program_id
JOIN snapshot s         ON s.snapshot_id = pv.first_seen_snapshot_id
GROUP BY a.asset_id
"""

# Derived edges, born_on-stamped + directed both ways (so related(src) is a plain WHERE).
#  - mechanism : two distinct assets share a target  -> de-risk/doubt readthrough
#  - competitive: share an indication but NO shared target -> share-shift readthrough
_MATVIEW_EDGE = f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS {SCHEMA}.program_edge AS
WITH at AS (SELECT DISTINCT asset_id, target_id FROM asset_target),
     ai AS (SELECT DISTINCT asset_id, indication_id FROM program),
mechanism AS (
    SELECT a.asset_id AS src_program_id, b.asset_id AS dst_program_id,
           'mechanism'::text AS link_type, a.target_id AS shared_id, 1.0::numeric AS relatedness
    FROM at a JOIN at b ON a.target_id = b.target_id AND a.asset_id <> b.asset_id
),
competitive AS (
    SELECT a.asset_id AS src_program_id, b.asset_id AS dst_program_id,
           'competitive'::text AS link_type, a.indication_id AS shared_id, 0.7::numeric AS relatedness
    FROM ai a JOIN ai b ON a.indication_id = b.indication_id AND a.asset_id <> b.asset_id
    WHERE NOT EXISTS (
        SELECT 1 FROM at ta JOIN at tb ON ta.target_id = tb.target_id
        WHERE ta.asset_id = a.asset_id AND tb.asset_id = b.asset_id
    )
),
edges AS (SELECT * FROM mechanism UNION ALL SELECT * FROM competitive)
SELECT e.src_program_id, e.dst_program_id, e.link_type, e.shared_id, e.relatedness,
       GREATEST(bs.born_on, bd.born_on) AS born_on
FROM edges e
LEFT JOIN {SCHEMA}.program_born_on bs ON bs.program_id = e.src_program_id
LEFT JOIN {SCHEMA}.program_born_on bd ON bd.program_id = e.dst_program_id
"""

# Unique indexes enable REFRESH ... CONCURRENTLY (no read lock during refresh).
_MATVIEW_INDEXES = [
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_program_born_on ON {SCHEMA}.program_born_on (program_id)",
    f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_program_edge
        ON {SCHEMA}.program_edge (src_program_id, dst_program_id, link_type, shared_id)""",
    f"CREATE INDEX IF NOT EXISTS ix_program_edge_src ON {SCHEMA}.program_edge (src_program_id)",
]

MATVIEWS = ["program_born_on", "program_edge"]  # refresh order matters (edge depends on born_on)


def grant_sql() -> list[str]:
    """Idempotent role creation + least-privilege grants (published schema only)."""
    return [
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{READER_ROLE}') THEN
                CREATE ROLE {READER_ROLE} LOGIN PASSWORD '{READER_PASSWORD}';
            END IF;
        END $$;
        """,
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO {READER_ROLE}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {READER_ROLE}",  # includes views
        # Materialized views need explicit grants (not covered by ALL TABLES on some PG versions).
        f"GRANT SELECT ON {SCHEMA}.program_born_on TO {READER_ROLE}",
        f"GRANT SELECT ON {SCHEMA}.program_edge TO {READER_ROLE}",
        # Future views in the schema are auto-granted.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT SELECT ON TABLES TO {READER_ROLE}",
    ]


def create_sql() -> list[str]:
    """All DDL to (re)create the published schema, in dependency order."""
    stmts = [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"]
    stmts.append(_MATVIEW_BORN_ON)   # before program view + edge matview
    stmts.append(_MATVIEW_EDGE)
    stmts.extend(_MATVIEW_INDEXES)
    stmts.extend(sql for _, sql in _VIEWS)
    return stmts
