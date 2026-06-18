from __future__ import annotations

import json
import subprocess
import sys

import typer

app = typer.Typer(help="Pharma pipeline intelligence — ingestion & data CLI", no_args_is_help=True)


@app.command()
def db_upgrade() -> None:
    """Apply Alembic migrations up to head."""
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


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
    text_only: bool = typer.Option(False, "--text-only", help="Do not send screenshots to the extractor"),
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
        outcome = extract_snapshot(
            s,
            storage,
            snapshot,
            model or DEFAULT_EXTRACTION_MODEL,
            include_screenshots=not text_only,
        )
    typer.echo(json.dumps(outcome.__dict__, indent=2, default=str))


batch_app = typer.Typer(help="Anthropic Message Batch workflows")
app.add_typer(batch_app, name="model-batch")


@batch_app.command(name="submit-extractions")
def model_batch_submit_extractions(
    limit: int = typer.Option(10, "--limit", "-n", help="Changed snapshots to submit"),
    company: str = typer.Option(None, "--company", "-c", help="Optional company/ticker filter"),
) -> None:
    """Submit pending changed snapshots to Anthropic Message Batches for 50%-discount extraction."""
    from pipeline_intel.db import session
    from pipeline_intel.extract.batch_api import submit_extraction_batch
    from pipeline_intel.ingest.storage import get_storage

    with session() as s:
        result = submit_extraction_batch(s, get_storage(), limit=limit, company=company)
    typer.echo(json.dumps(result, indent=2, default=str))


@batch_app.command(name="status")
def model_batch_status(
    batch_id: str = typer.Argument(..., help="Local model_batch_id or Anthropic provider batch id"),
) -> None:
    """Refresh and show an Anthropic Message Batch status."""
    from pipeline_intel.db import session
    from pipeline_intel.extract.batch_api import refresh_batch_status

    with session() as s:
        result = refresh_batch_status(s, batch_id)
    typer.echo(json.dumps(result, indent=2, default=str))


@batch_app.command(name="collect-extractions")
def model_batch_collect_extractions(
    batch_id: str = typer.Argument(..., help="Local model_batch_id or Anthropic provider batch id"),
) -> None:
    """Collect ended batch extraction results into silver extraction rows."""
    from pipeline_intel.db import session
    from pipeline_intel.extract.batch_api import collect_extraction_batch

    with session() as s:
        result = collect_extraction_batch(s, batch_id)
    typer.echo(json.dumps(result, indent=2, default=str))


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
def qa(
    company: str = typer.Option(None, "--company", "-c", help="Company name/ticker to QA"),
    extraction: str = typer.Option(None, "--extraction", "-e", help="Extraction ID to QA"),
    model: str = typer.Option(None, "--model", "-m", help="Override QA judge model"),
) -> None:
    """Run autonomous QA for an extraction. Needs ANTHROPIC_API_KEY unless deterministic checks fail first."""
    from sqlalchemy import select

    from pipeline_intel.batch import resolve_company
    from pipeline_intel.db import session
    from pipeline_intel.gold.models import CompanySource, Extraction, Snapshot
    from pipeline_intel.ingest.storage import get_storage
    from pipeline_intel.quality.checker import QA_JUDGE_MODEL, run_quality_check

    storage = get_storage()
    with session() as s:
        extraction_id = extraction
        if not extraction_id and company:
            c = resolve_company(s, company)
            if c is None:
                typer.echo(f"no company matching {company!r}")
                raise typer.Exit(1)
            extraction_id = s.execute(
                select(Extraction.extraction_id)
                .join(Snapshot, Snapshot.snapshot_id == Extraction.snapshot_id)
                .join(CompanySource, CompanySource.source_id == Snapshot.source_id)
                .where(CompanySource.company_id == c.company_id, Extraction.raw_json.isnot(None))
                .order_by(Extraction.extracted_at.desc())
                .limit(1)
            ).scalar_one_or_none()
        if not extraction_id:
            typer.echo("provide --extraction or --company")
            raise typer.Exit(1)
        outcome = run_quality_check(s, storage, extraction_id, model=model or QA_JUDGE_MODEL)
    typer.echo(json.dumps(outcome.as_dict(), indent=2, default=str))


@app.command()
def batch(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of companies to process"),
    status: str = typer.Option("ready", "--status", help="pipeline_status to select, or ready"),
    publish_mode: str = typer.Option("gated", "--publish-mode", help="gated or ungated"),
    routing: str = typer.Option("smart", "--routing", help="smart | cheap | quality"),
    escalate_opus: bool = typer.Option(True, "--escalate-opus/--no-escalate-opus"),
) -> None:
    """Run a gated scrape/extract/QA/load batch."""
    from pipeline_intel.batch import run_batch

    result = run_batch(
        limit=limit,
        status=status,
        publish_mode=publish_mode,
        routing=routing,
        escalate_opus=escalate_opus,
    )
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command(name="cost-estimate")
def cost_estimate(
    companies: int = typer.Option(600, "--companies", "-n", help="Number of companies"),
    batch_api: bool = typer.Option(True, "--batch-api/--no-batch-api"),
    small_share: float = typer.Option(0.50, "--small-share", help="Tiny/simple company share"),
    normal_share: float = typer.Option(0.35, "--normal-share", help="Normal company share"),
    high_value_share: float = typer.Option(0.10, "--high-value-share", help="High-value company share"),
    complex_share: float = typer.Option(0.05, "--complex-share", help="Large/complex company share"),
) -> None:
    """Estimate routed token cost for a company universe."""
    from pipeline_intel.extract.client import HAIKU_MODEL, OPUS_MODEL, SONNET_MODEL
    from pipeline_intel.model_routing import ModelRoute, estimate_company_cost

    routes = {
        "small": ModelRoute(
            extraction_model=SONNET_MODEL,
            qa_model=HAIKU_MODEL,
            escalation_model=SONNET_MODEL,
            complexity="small",
            reason="small pipeline",
        ),
        "normal": ModelRoute(
            extraction_model=SONNET_MODEL,
            qa_model=SONNET_MODEL,
            escalation_model=OPUS_MODEL,
            complexity="normal",
            reason="normal pipeline",
        ),
        "high_value": ModelRoute(
            extraction_model=SONNET_MODEL,
            qa_model=SONNET_MODEL,
            escalation_model=OPUS_MODEL,
            complexity="high_value",
            reason="high-value appellate QA",
        ),
        "complex": ModelRoute(
            extraction_model=OPUS_MODEL,
            qa_model=SONNET_MODEL,
            escalation_model=OPUS_MODEL,
            complexity="complex",
            reason="large/image-heavy pipeline",
        ),
    }
    shares = {
        "small": small_share,
        "normal": normal_share,
        "high_value": high_value_share,
        "complex": complex_share,
    }
    total_share = sum(shares.values())
    if abs(total_share - 1.0) > 0.001:
        typer.echo(f"shares must sum to 1.0, got {total_share:.3f}")
        raise typer.Exit(1)

    estimates = {}
    total = 0.0
    for name, route in routes.items():
        est = estimate_company_cost(route, batch=batch_api)
        count = round(companies * shares[name])
        subtotal = count * est["total_cost_usd"]
        estimates[name] = {"companies": count, **est, "subtotal_usd": round(subtotal, 2)}
        total += subtotal
    typer.echo(json.dumps({
        "companies": companies,
        "batch_api": batch_api,
        "segments": estimates,
        "total_usd": round(total, 2),
    }, indent=2, default=str))


@app.command(name="repair")
def repair(
    company: str = typer.Option(..., "--company", "-c", help="Company name/ticker to mark for repair"),
) -> None:
    """Prepare a company for repair-mode rerendering on the next batch."""
    from pipeline_intel.batch import repair_company

    result = repair_company(company)
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command(name="source-discover")
def source_discover(
    company: str = typer.Option(..., "--company", "-c", help="Company name/ticker to inspect"),
    persist: bool = typer.Option(False, "--persist", help="Insert discovered candidate sources"),
) -> None:
    """Discover candidate pipeline files/URLs from already-captured artifacts."""
    from pipeline_intel.db import session
    from pipeline_intel.ingest.storage import get_storage
    from pipeline_intel.source_discovery import discover_company_sources

    with session() as s:
        result = discover_company_sources(s, get_storage(), company, persist=persist)
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command(name="rebuild-history")
def rebuild_history_cmd(
    company: str = typer.Option(..., "--company", "-c", help="Company name (substring) or company_id"),
    confirm_n: int = typer.Option(2, "--confirm-n", help="Consecutive absences to confirm a discontinuation"),
) -> None:
    """Recompute a company's longitudinal change-event feed from silver (chronological replay
    over snapshots ordered by captured_at). Idempotent: rewrites this company's change_event rows."""
    from sqlalchemy import or_, select

    from pipeline_intel.db import session
    from pipeline_intel.gold.models import Company
    from pipeline_intel.history.rebuild import rebuild_history

    with session() as s:
        cid = s.execute(
            select(Company.company_id)
            .where(or_(Company.company_id == company, Company.name.ilike(f"%{company}%")))
            .limit(1)
        ).scalar_one_or_none()
        if not cid:
            typer.echo(f"no company matching {company!r}")
            raise typer.Exit(1)
        stats = rebuild_history(s, cid, confirm_n=confirm_n)
    typer.echo(json.dumps(stats, indent=2, default=str))


@app.command(name="load-wayback-history")
def load_wayback_history_cmd(
    company: str = typer.Option("Bristol Myers Squibb", "--company", "-c", help="Company name"),
    extractions_dir: str = typer.Option(
        "experiments/wayback_bms_poc/extractions", "--dir", help="Dir of quarterly extraction JSONs"),
    aliases: str = typer.Option(
        "experiments/wayback_bms_poc/curated_aliases.json", "--aliases",
        help="Curated rename-merge clusters to seed asset_alias (set '' to skip)"),
) -> None:
    """Ingest the Wayback POC extractions into the DB (snapshots+extractions+gold), seed the alias
    decision store, and rebuild the change-event feed. Idempotent."""
    from pipeline_intel.db import session
    from pipeline_intel.history.ingest_poc import load_wayback_history

    with session() as s:
        stats = load_wayback_history(s, company, extractions_dir, aliases or None)
    typer.echo(json.dumps(stats, indent=2, default=str))


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
