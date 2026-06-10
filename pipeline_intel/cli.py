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
) -> None:
    """Ingest one company: fetch -> render -> snapshot (bronze). Extraction lands in M1."""
    from pipeline_intel.ingest.run import run_company

    result = run_company(company)
    typer.echo(json.dumps(result, indent=2, default=str))


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
