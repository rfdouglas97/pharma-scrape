# Published DB — live read-only access for Project Rand

The `published` schema is the **stable contract surface** the quant trading system
(`robinhood` / Project Rand) reads directly over a **live, read-only Postgres connection**.
It is shaped to satisfy `robinhood/design/pipeline_db_spec.md`, so the trading side's
`PipelineDBGraph` (`as_of(date)` + `related(source)`) wires up with no engine changes.

**Why direct SQL, not the REST API:** the backtest does huge numbers of `related()` lookups —
a per-call HTTP round-trip is too slow, and a frozen weekly file isn't live enough for
execution. Postgres serves concurrent readers while the pipeline keeps writing, and the SCD2
history + `born_on` columns make the *live* DB answer both "what's true now" (live trading)
and "what did we know as-of date X" (backtest). The `/v1` FastAPI stays for browsing only.

## Connect

Read-only role `rand_reader` (localhost dev password below — set a real one before exposing
off-box: `ALTER ROLE rand_reader PASSWORD '…'`). It can `SELECT` from `published.*` only; the
internal gold tables are not visible to it.

```
postgresql://rand_reader:rand_reader@localhost:5433/pipeline_intel
```

The trading repo runs DuckDB. Attach Postgres read-only and query `published.*` as if local —
and join it against `companies.duckdb` on `ticker` in one statement:

```python
import duckdb
con = duckdb.connect("companies.duckdb")
con.execute("INSTALL postgres; LOAD postgres;")
con.execute(
    "ATTACH 'postgresql://rand_reader:rand_reader@localhost:5433/pipeline_intel' "
    "AS pipe (TYPE postgres, READ_ONLY)"
)
# programs hitting a target, joined to the tradable universe
con.sql("""
  SELECT pc.ticker, p.drug_name, p.phase, t.hgnc_symbol
  FROM pipe.published.program_target pt
  JOIN pipe.published.program p  ON p.program_id = pt.program_id
  JOIN pipe.published.target  t  ON t.target_id  = pt.target_id
  JOIN pipe.published.program_company pc ON pc.program_id = p.program_id
  JOIN company c ON c.ticker = pc.ticker          -- companies.duckdb
  WHERE t.hgnc_symbol = 'PDCD1'
""").show()
```

## Schema (`published.*`)

A spec's "program" == one **drug/asset**, rolled up to its owning company. Per-indication
phase/status + SCD2 validity live in `program_indication` (the point-in-time table).

| Object | Grain | Key columns |
|---|---|---|
| `program` | drug/asset | `program_id`, `ticker`, `drug_name`, `modality`, `moa`, `phase`, `status`, `born_on`, `chembl_id` |
| `program_indication` | drug × indication × company (SCD2) | `program_id`, `indication_id`, `phase`, `status`, **`valid_from`/`valid_to`** |
| `program_target` | drug × target | `program_id`, `target_id`, `role`, `source` (disclosed\|open_targets) |
| `program_moa` | drug | `program_id`, `moa` (free text) |
| `program_company` | drug × company (m:m) | `program_id`, `company_id`, `ticker`, `role` (sponsor\|partner) |
| `target` | target | `target_id`, `hgnc_symbol`, `uniprot_id`, `ensembl_id`, `name` |
| `indication` | indication | `indication_id`, `name`, `mondo_id`, `therapeutic_area`, `parent_ids[]` |
| `drug_alias` | alias | `program_id`, `alias`, `alias_type` (dev_code\|brand\|generic\|preferred\|durable) |
| `program_born_on` *(matview)* | drug | `program_id`, `born_on`, `born_on_source` |
| `program_edge` *(matview)* | drug → drug | `src_program_id`, `dst_program_id`, `link_type` (mechanism\|competitive), `shared_id`, `relatedness`, `born_on` |

**`program_id` == the asset's ULID**, and `drug_alias.program_id` joins to it — so an event's
drug name resolves via `drug_alias` → `program_id` → `program_edge`.

### Point-in-time (`as_of`) — no look-ahead

- **Edges / nodes:** filter `born_on <= t0`. `born_on` is the earliest snapshot the program was
  disclosed in (public-disclosure anchor; extended backward by the Wayback backfill). Run
  `pipeline run-wayback`/backfill to push history earlier.
- **Per-indication state:** filter `valid_from <= t0 AND (valid_to IS NULL OR valid_to > t0)`
  on `program_indication`.

### Edges

`program_edge` is directed both ways, so `related(src)` is a plain `WHERE src_program_id = ?`:
- `mechanism` — two drugs share a **target** (de-risk / doubt readthrough). `shared_id` = target.
- `competitive` — share an **indication** but no shared target (share-shift). `shared_id` = indication.

## Drop-in `PipelineDBGraph` (trading side)

Satisfies `robinhood/backtest/interfaces.py::Graph`; swap `InterimTagGraph` → this, no other
engine changes:

```python
class PipelineDBGraph(Graph):
    def __init__(self, conn, as_of_date=None):
        self.conn = conn            # DuckDB conn with `pipe` attached (see above)
        self._as_of = as_of_date

    def as_of(self, date):
        return PipelineDBGraph(self.conn, date)

    def related(self, source):
        # source carries a program_id (resolve from a drug name via published.drug_alias upstream)
        clause = "AND e.born_on <= ?" if self._as_of else ""
        params = [source.program_id] + ([self._as_of] if self._as_of else [])
        return self.conn.execute(f"""
            SELECT e.dst_program_id, e.link_type, e.relatedness, p.drug_name, pc.ticker
            FROM pipe.published.program_edge e
            JOIN pipe.published.program p ON p.program_id = e.dst_program_id
            JOIN pipe.published.program_company pc ON pc.program_id = e.dst_program_id
            WHERE e.src_program_id = ? {clause}
        """, params).fetchall()
```

## Refresh / freshness

The two materialized views (`program_born_on`, `program_edge`) refresh after each cycle; the
views are always live.

```bash
uv run pipeline publish --init   # (re)create schema, views, matviews, rand_reader role + grants
uv run pipeline publish          # refresh the matviews (run after each ingest/enrich cycle)
```

`--init` is idempotent (drops + recreates `published`). Wire `pipeline publish` into the tail of
your weekly `batch`/`enrich` cron so the read surface tracks the latest scrape.

## Known gaps (contract columns present, not yet populated)

- `cik`, `is_lead`, `is_lead_indication` — columns exist for the spec; NULL until tracked.
- `born_on` is **disclosure-anchored** (first time we saw the program), not trial-registration.
  If a backtest needs earlier/precise dates, the follow-up is a ClinicalTrials.gov join on
  `drug_alias` → `StudyFirstPostDate`.
