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
from pipeline_intel.ingest.classify import classify_rendered_page, sniff_url_type
from pipeline_intel.ingest.fetch_doc import DocFetchError, fetch_document
from pipeline_intel.ingest.parse_doc import parse_document
from pipeline_intel.ingest.render import RenderError, render_with_fallback, robots_allows
from pipeline_intel.ingest.snapshot import write_doc_snapshot, write_snapshot
from pipeline_intel.ingest.storage import Storage, get_storage
from pipeline_intel.source_discovery import discover_from_html, select_promotable_file

DOC_SOURCE_TYPES = {"csv_doc", "xlsx_doc", "pdf_doc"}


def _try_promote_file(s, storage: Storage, source_id: str, slug: str, base_url: str,
                      html: str, page_kind: str):
    """If the rendered page links a better-ranked pipeline file (xlsx/pdf/csv), ingest THAT
    via the document path and return (snapshot, changed, source_type, url). Else None.
    The page is rendered every run (for discovery + change detection); when a file wins we
    persist only the cleaner doc snapshot under this source."""
    choice = select_promotable_file(discover_from_html(base_url, html), page_kind)
    if choice is None:
        return None
    try:
        doc = fetch_document(choice["url"])
        parsed = parse_document(doc.raw_bytes, content_type=doc.content_type, ext=doc.ext)
    except (DocFetchError, ValueError):
        return None  # fall back to the page on any fetch/parse failure
    snap, changed = write_doc_snapshot(s, storage, source_id, slug, doc, parsed)
    return snap, changed, choice["source_type"], choice["url"]


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
            ).order_by(CompanySource.preferred_source_rank, CompanySource.added_at)
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
            doc_type = src.source_type if src.source_type in DOC_SOURCE_TYPES else sniff_url_type(src.url)
            try:
                if doc_type:
                    doc = fetch_document(src.url)
                    parsed = parse_document(doc.raw_bytes, content_type=doc.content_type, ext=doc.ext)
                    snap, did_change = write_doc_snapshot(s, storage, src.source_id, slug, doc, parsed)
                    if not src.source_type:
                        src.source_type = doc_type
                    kind = doc_type
                else:
                    result = render_with_fallback(src.url, src.render_config)
                    # Classify the rendered page from real evidence so model routing reacts.
                    page_kind = classify_rendered_page(
                        result.html, result.text, result.meta.get("pipeline_image_urls")
                    )
                    # Format selection: if the page links a cleaner pipeline file, ingest that.
                    promoted = _try_promote_file(
                        s, storage, src.source_id, slug, result.url, result.html, page_kind
                    )
                    if promoted is not None:
                        snap, did_change, kind, promoted_url = promoted
                        entry["promoted_to_file"] = promoted_url
                    else:
                        snap, did_change = write_snapshot(s, storage, src.source_id, slug, result)
                        kind = page_kind
                    src.source_type = kind
                company.pipeline_status = "render_ok"
                entry |= {
                    "status": "ok",
                    "source_type": kind,
                    "snapshot_id": snap.snapshot_id,
                    "http_status": snap.http_status,
                    "content_hash": snap.content_hash[:12],
                    "changed": did_change,
                }
                ok += 1
                changed += int(did_change)
                unchanged += int(not did_change)
            except (RenderError, DocFetchError, ValueError) as exc:
                entry |= {"status": "render_failed", "error": str(exc)}
                company.pipeline_status = "failed"
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
