from __future__ import annotations

import json
import subprocess

import typer

app = typer.Typer(help="Pharma pipeline intelligence — ingestion & data CLI", no_args_is_help=True)


@app.command()
def db_upgrade() -> None:
    """Apply Alembic migrations up to head."""
    subprocess.run(["alembic", "upgrade", "head"], check=True)


@app.command()
def seed() -> None:
    """Load controlled vocabularies and the company registry (idempotent)."""
    from pipeline_intel.registry.seed import seed_all

    stats = seed_all()
    typer.echo(json.dumps(stats, indent=2))


@app.command()
def run(
    company: str = typer.Option(..., "--company", "-c", help="Company name (exact or substring)"),
    extract: bool = typer.Option(False, "--extract", help="Also run extraction on changed snapshots"),
) -> None:
    """Ingest one company: fetch -> render -> snapshot (bronze). With --extract, chain
    LLM extraction (silver) on any changed snapshot (needs ANTHROPIC_API_KEY)."""
    from pipeline_intel.ingest.run import run_company

    result = run_company(company)

    if extract:
        from pipeline_intel.db import session
        from pipeline_intel.extract.extractor import extract_snapshot
        from pipeline_intel.ingest.storage import get_storage

        storage = get_storage()
        extractions = []
        with session() as s:
            for src in result.get("sources", []):
                if src.get("status") == "ok" and src.get("changed") and src.get("snapshot_id"):
                    outcome = extract_snapshot(s, storage, src["snapshot_id"])
                    extractions.append({"snapshot_id": src["snapshot_id"], **outcome.__dict__})
        result["extractions"] = extractions

    typer.echo(json.dumps(result, indent=2, default=str))


@app.command()
def extract(
    snapshot: str = typer.Option(None, "--snapshot", "-s", help="Snapshot ID to extract"),
    company: str = typer.Option(None, "--company", "-c", help="Extract latest snapshot for company"),
    model: str = typer.Option(None, "--model", "-m", help="Override extraction model"),
) -> None:
    """Run LLM extraction (silver) on a snapshot's artifacts. Needs ANTHROPIC_API_KEY."""
    from pipeline_intel.db import session
    from pipeline_intel.extract.client import DEFAULT_EXTRACTION_MODEL
    from pipeline_intel.extract.extractor import extract_snapshot
    from pipeline_intel.ingest.storage import get_storage
    from pipeline_intel.quality.golden import latest_snapshot_for_company

    if not snapshot and company:
        snapshot = latest_snapshot_for_company(company)
        if not snapshot:
            typer.echo(f"no snapshot with artifacts found for {company!r}")
            raise typer.Exit(1)
    if not snapshot:
        typer.echo("provide --snapshot or --company")
        raise typer.Exit(1)

    storage = get_storage()
    with session() as s:
        outcome = extract_snapshot(s, storage, snapshot, model or DEFAULT_EXTRACTION_MODEL)
    typer.echo(json.dumps(outcome.__dict__, indent=2, default=str))


@app.command()
def load(
    extraction: str = typer.Option(None, "--extraction", "-e", help="Extraction ID to load"),
    company: str = typer.Option(None, "--company", "-c", help="Load latest extraction for company"),
    all_: bool = typer.Option(False, "--all", help="Load the latest extraction for every snapshot"),
) -> None:
    """Load extraction(s) from silver into gold (company/asset/program + SCD2). Idempotent."""
    from sqlalchemy import select

    from pipeline_intel.db import session
    from pipeline_intel.gold.models import Extraction
    from pipeline_intel.gold.upsert import load_extraction
    from pipeline_intel.quality.golden import latest_snapshot_for_company

    with session() as s:
        ext_ids: list[str] = []
        if extraction:
            ext_ids = [extraction]
        elif company:
            snap_id = latest_snapshot_for_company(company)
            if not snap_id:
                typer.echo(f"no snapshot for {company!r}")
                raise typer.Exit(1)
            eid = s.execute(
                select(Extraction.extraction_id)
                .where(Extraction.snapshot_id == snap_id, Extraction.raw_json.isnot(None))
                .order_by(Extraction.extracted_at.desc()).limit(1)
            ).scalar_one_or_none()
            ext_ids = [eid] if eid else []
        elif all_:
            # latest non-empty extraction per snapshot
            rows = s.execute(
                select(Extraction.extraction_id, Extraction.snapshot_id, Extraction.extracted_at)
                .where(Extraction.raw_json.isnot(None))
                .order_by(Extraction.extracted_at.desc())
            ).all()
            seen: set[str] = set()
            for eid, sid, _ in rows:
                if sid not in seen:
                    seen.add(sid)
                    ext_ids.append(eid)
        else:
            typer.echo("provide --extraction, --company, or --all")
            raise typer.Exit(1)

        results = [load_extraction(s, eid).as_dict() for eid in ext_ids if eid]
    typer.echo(json.dumps({"loaded": len(results), "results": results}, indent=2))


@app.command()
def enrich(
    model: str = typer.Option(None, "--model", "-m", help="Override mapping model"),
    limit: int = typer.Option(None, "--limit", help="Map at most N indications (testing)"),
    skip_closure: bool = typer.Option(False, "--skip-closure", help="Map only; don't rebuild closure"),
    closure_only: bool = typer.Option(False, "--closure-only", help="Skip mapping; rebuild closure only"),
) -> None:
    """Enrich gold: map indications -> EFO/MONDO and build the adjacency closure.
    Both steps commit incrementally and are resumable. Needs ANTHROPIC_API_KEY (mapping)."""
    from pipeline_intel.db import session
    from pipeline_intel.ontology.closure import build_graph
    from pipeline_intel.ontology.mapper import MAPPER_MODEL, map_all

    result: dict = {}
    if not closure_only:
        with session() as s:
            result["mapping"] = map_all(s, model or MAPPER_MODEL, limit=limit).as_dict()
    if not skip_closure:
        with session() as s:
            result["closure"] = build_graph(s).as_dict()
    typer.echo(json.dumps(result, indent=2))


@app.command(name="backfill-ot")
def backfill_ot(limit: int = typer.Option(None, "--limit", help="Backfill at most N assets")) -> None:
    """Backfill target/modality/mechanism from Open Targets for assets where the company
    didn't disclose them. Provenance-tagged source=open_targets; never overwrites disclosed."""
    from pipeline_intel.db import session
    from pipeline_intel.ontology.open_targets import backfill_all

    with session() as s:
        stats = backfill_all(s, limit=limit)
    typer.echo(json.dumps(stats.as_dict(), indent=2))


@app.command(name="classify-ta")
def classify_ta() -> None:
    """Assign each mapped indication a therapeutic area from the MONDO hierarchy. Uses OLS."""
    from pipeline_intel.db import session
    from pipeline_intel.ontology.therapeutic_area import classify_all

    with session() as s:
        stats = classify_all(s)
    typer.echo(json.dumps(stats.as_dict(), indent=2))


@app.command(name="eval")
def eval_cmd(model: str = typer.Option(None, "--model", "-m", help="Override model")) -> None:
    """Run the golden-set evaluation and apply the M1 quality gate. Needs ANTHROPIC_API_KEY."""
    from pipeline_intel.quality.eval_harness import run_eval

    report = run_eval(model)
    typer.echo(json.dumps(report, indent=2))
    if report["n_fixtures"] == 0:
        typer.echo("no golden fixtures found — scaffold some with `pipeline golden-scaffold`")
        raise typer.Exit(1)
    raise typer.Exit(0 if report["gate_pass"] else 1)


@app.command()
def golden_scaffold(
    company: str = typer.Option(None, "--company", "-c", help="Scaffold from company's latest snapshot"),
    snapshot: str = typer.Option(None, "--snapshot", "-s", help="Scaffold from a specific snapshot"),
    fmt: str = typer.Option("unknown", "--format", "-f", help="html_table | js | pdf | image"),
) -> None:
    """Create a golden-set fixture (page text + screenshot + label template) from a snapshot."""
    from pipeline_intel.quality.golden import latest_snapshot_for_company, scaffold_from_snapshot

    if not snapshot and company:
        snapshot = latest_snapshot_for_company(company)
        if not snapshot:
            typer.echo(f"no snapshot with artifacts for {company!r} — run `pipeline run` first")
            raise typer.Exit(1)
    if not snapshot:
        typer.echo("provide --snapshot or --company")
        raise typer.Exit(1)
    typer.echo(json.dumps(scaffold_from_snapshot(snapshot, fmt), indent=2))


@app.command()
def companies() -> None:
    """List companies in the registry with their source URLs."""
    from sqlalchemy import select

    from pipeline_intel.db import session
    from pipeline_intel.gold.models import Company, CompanySource

    with session() as s:
        for c in s.execute(select(Company).order_by(Company.name)).scalars():
            srcs = s.execute(
                select(CompanySource.url).where(CompanySource.company_id == c.company_id)
            ).scalars().all()
            typer.echo(f"{c.name} ({c.ticker or '-'})")
            for u in srcs:
                typer.echo(f"    {u}")


if __name__ == "__main__":
    app()
