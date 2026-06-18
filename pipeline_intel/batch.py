"""Batch orchestration for the live pharma pipeline factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pipeline_intel.extract.extractor import extract_snapshot
from pipeline_intel.gold.models import Company, CompanySource, JobRun, Snapshot
from pipeline_intel.gold.upsert import load_extraction
from pipeline_intel.ingest.run import run_company
from pipeline_intel.ingest.storage import get_storage
from pipeline_intel.model_routing import (
    HAIKU_MODEL,
    SONNET_MODEL,
    ModelRoute,
    route_for_company_source,
)
from pipeline_intel.quality.checker import run_quality_check

READY_STATUSES = ("unverified_source", "needs_repair", "render_ok", "extraction_ok")


@dataclass
class CompanyBatchResult:
    company: str
    status: str
    ingest: dict = field(default_factory=dict)
    extractions: list[dict] = field(default_factory=list)
    qa: list[dict] = field(default_factory=list)
    loaded: list[dict] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def select_batch_companies(s: Session, limit: int = 10, status: str = "ready") -> list[Company]:
    stmt = select(Company).where(Company.status == "active").order_by(Company.name)
    if status == "ready":
        stmt = stmt.where(Company.pipeline_status.in_(READY_STATUSES))
    else:
        stmt = stmt.where(Company.pipeline_status == status)
    return list(s.execute(stmt.limit(limit)).scalars())


def resolve_company(s: Session, company_query: str) -> Company | None:
    return s.execute(
        select(Company).where(
            or_(
                Company.company_id == company_query,
                Company.ticker == company_query.upper(),
                Company.name == company_query,
                Company.name.ilike(f"%{company_query}%"),
            )
        ).order_by(Company.name).limit(1)
    ).scalar_one_or_none()


def run_company_pipeline(
    company_query: str,
    publish_mode: str = "gated",
    routing: str = "smart",
    escalate_opus: bool = True,
    judge=None,
) -> CompanyBatchResult:
    storage = get_storage()
    result = CompanyBatchResult(company=company_query, status="running")
    try:
        ingest = run_company(company_query)
        result.ingest = ingest
        if ingest.get("error"):
            result.status = "failed"
            result.error = ingest["error"]
            return result

        with _session() as s:
            company = resolve_company(s, ingest["company"])
            if company:
                company.pipeline_status = (
                    "render_ok" if ingest.get("status") in ("ok", "partial") else "failed"
                )
            changed_sources = [
                src for src in ingest.get("sources", [])
                if src.get("status") == "ok" and src.get("changed") and src.get("snapshot_id")
            ]
            for src in changed_sources:
                route = _route_for_snapshot(s, src["snapshot_id"], routing)
                result.routes.append({"snapshot_id": src["snapshot_id"], **route.as_dict()})
                outcome = extract_snapshot(
                    s, storage, src["snapshot_id"], model=route.extraction_model,
                )
                result.extractions.append({
                    "snapshot_id": src["snapshot_id"],
                    "model_route": route.as_dict(),
                    **outcome.__dict__,
                })
                if outcome.extraction_id and outcome.status in ("ok", "needs_review"):
                    if company:
                        company.pipeline_status = "extraction_ok"
                    qa = run_quality_check(
                        s, storage, outcome.extraction_id, judge=judge, model=route.qa_model,
                    )
                    if (
                        escalate_opus
                        and route.escalation_model
                        and qa.verdict == "fail"
                        and judge is None
                    ):
                        qa = run_quality_check(
                            s, storage, outcome.extraction_id, model=route.escalation_model,
                        )
                    result.qa.append(qa.as_dict())
                    if publish_mode == "gated" and qa.verdict not in ("pass", "warn"):
                        continue
                    if outcome.status == "ok":
                        loaded = load_extraction(s, outcome.extraction_id)
                        result.loaded.append(loaded.as_dict())
                        if company:
                            company.pipeline_status = "loaded_gold"

            if not changed_sources and company:
                company.pipeline_status = (
                    "loaded_gold" if ingest.get("status") == "ok" else company.pipeline_status
                )
        if result.loaded:
            result.status = "loaded_gold"
        elif result.qa and all(q["verdict"] in ("pass", "warn") for q in result.qa):
            result.status = "qa_passed"
        elif result.qa:
            result.status = "needs_repair"
        else:
            result.status = ingest.get("status", "ok")
    except Exception as exc:  # noqa: BLE001 - batch isolates company failures
        result.status = "failed"
        result.error = str(exc)
    return result


def run_batch(
    limit: int = 10,
    status: str = "ready",
    publish_mode: str = "gated",
    routing: str = "smart",
    escalate_opus: bool = True,
    judge=None,
) -> dict:
    from pipeline_intel.db import session

    with session() as s:
        companies = select_batch_companies(s, limit=limit, status=status)
        job = JobRun(
            kind="batch",
            status="running",
            stats={
                "limit": limit,
                "status": status,
                "routing": routing,
                "escalate_opus": escalate_opus,
            },
        )
        s.add(job)
        s.flush()
        company_names = [c.name for c in companies]
        job_id = job.run_id

    results = [
        run_company_pipeline(
            name,
            publish_mode=publish_mode,
            routing=routing,
            escalate_opus=escalate_opus,
            judge=judge,
        )
        for name in company_names
    ]

    with session() as s:
        job = s.get(JobRun, job_id)
        if job:
            failed = sum(1 for r in results if r.status == "failed")
            needs_repair = sum(1 for r in results if r.status == "needs_repair")
            loaded = sum(1 for r in results if r.status == "loaded_gold")
            job.status = "ok" if failed == 0 and needs_repair == 0 else ("partial" if loaded else "failed")
            job.finished_at = datetime.now(UTC)
            job.stats = {
                "companies": len(results),
                "loaded": loaded,
                "needs_repair": needs_repair,
                "failed": failed,
            }

    return {"run_id": job_id, "companies": [r.as_dict() for r in results]}


def repair_company(company_query: str) -> dict:
    from pipeline_intel.db import session

    with session() as s:
        company = resolve_company(s, company_query)
        if company is None:
            return {"error": f"no company matching {company_query!r}"}
        company.pipeline_status = "needs_repair"
        sources = s.execute(
            select(CompanySource).where(CompanySource.company_id == company.company_id)
        ).scalars().all()
        for src in sources:
            cfg = dict(src.render_config or {})
            cfg.setdefault("wait_ms", 3000)
            cfg.setdefault("full_page", True)
            cfg["repair_mode"] = True
            src.render_config = cfg
        return {
            "company": company.name,
            "pipeline_status": company.pipeline_status,
            "sources_updated": len(sources),
        }


def _session():
    from pipeline_intel.db import session

    return session()


def _route_for_snapshot(s: Session, snapshot_id: str, routing: str) -> ModelRoute:
    snap = s.get(Snapshot, snapshot_id)
    if snap is None:
        raise ValueError(f"snapshot not found: {snapshot_id}")
    source = s.get(CompanySource, snap.source_id)
    if source is None or source.company is None:
        raise ValueError(f"source/company not found for snapshot: {snapshot_id}")
    if routing == "smart":
        return route_for_company_source(source.company, source)
    if routing == "cheap":
        return ModelRoute(
            extraction_model=SONNET_MODEL,
            qa_model=HAIKU_MODEL,
            escalation_model=SONNET_MODEL,
            complexity="cheap",
            reason="cheap routing: Sonnet extraction, Haiku QA, no Opus",
        )
    if routing == "quality":
        route = route_for_company_source(source.company, source)
        return ModelRoute(
            extraction_model=route.escalation_model or route.extraction_model,
            qa_model=route.qa_model,
            escalation_model=route.escalation_model,
            complexity=route.complexity,
            reason=f"quality routing: {route.reason}",
        )
    raise ValueError(f"unknown routing policy: {routing}")
