"""Per-company ingest orchestrator: resolve sources -> render -> snapshot, tracking a
job_run row. Each company is an independent, idempotent, rerun-safe job so one broken
site never blocks the weekly run.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from sqlalchemy import or_, select

from pipeline_intel.config import settings
from pipeline_intel.db import session
from pipeline_intel.gold.models import Company, CompanySource, JobRun
from pipeline_intel.ingest.render import RenderError, render, robots_allows
from pipeline_intel.ingest.snapshot import write_snapshot
from pipeline_intel.ingest.storage import get_storage


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def run_company(company_query: str) -> dict:
    storage = get_storage()
    s_settings = settings()

    with session() as s:
        company = s.execute(
            select(Company).where(
                or_(Company.name == company_query, Company.name.ilike(f"%{company_query}%"))
            ).order_by(Company.name).limit(1)
        ).scalar_one_or_none()
        if company is None:
            return {"error": f"no company matching {company_query!r}"}

        sources = s.execute(
            select(CompanySource).where(
                CompanySource.company_id == company.company_id,
                CompanySource.active.is_(True),
            )
        ).scalars().all()

        job = JobRun(kind="ingest", company_id=company.company_id, status="running")
        s.add(job)
        s.flush()

        slug = _slug(company.name)
        per_source: list[dict] = []
        ok = changed = unchanged = failed = 0

        for src in sources:
            entry: dict = {"url": src.url}
            allowed = robots_allows(src.url, s_settings.crawler_user_agent)
            if not allowed:
                entry["status"] = "blocked_by_robots"
                failed += 1
                per_source.append(entry)
                continue
            try:
                result = render(src.url, src.render_config)
                snap, did_change = write_snapshot(s, storage, src.source_id, slug, result)
                entry |= {
                    "status": "ok",
                    "snapshot_id": snap.snapshot_id,
                    "http_status": snap.http_status,
                    "content_hash": snap.content_hash[:12],
                    "changed": did_change,
                }
                ok += 1
                changed += int(did_change)
                unchanged += int(not did_change)
            except RenderError as exc:
                entry |= {"status": "render_failed", "error": str(exc)}
                failed += 1
            time.sleep(s_settings.crawler_delay_seconds)
            per_source.append(entry)

        job.status = "ok" if failed == 0 else ("partial" if ok else "failed")
        job.finished_at = datetime.now(UTC)
        job.stats = {
            "sources": len(sources),
            "ok": ok, "changed": changed, "unchanged": unchanged, "failed": failed,
        }

        return {
            "company": company.name,
            "run_id": job.run_id,
            "status": job.status,
            "stats": job.stats,
            "sources": per_source,
        }
