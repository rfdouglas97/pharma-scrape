"""Lightweight source discovery helpers.

This first pass records candidate pipeline files/URLs already visible in captured page
artifacts. Network search can be added later, but artifact-first discovery keeps the
workflow reproducible.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import Company, CompanySource, Snapshot
from pipeline_intel.ingest.storage import Storage

SOURCE_RANK = {
    "csv_doc": 5,
    "xlsx_doc": 10,
    "pdf_doc": 20,
    "html_table": 35,
    "pipeline_page": 50,
    "js_cards": 60,
    "image_page": 70,
}

FILE_TYPE_BY_EXT = {
    ".csv": "csv_doc",
    ".xlsx": "xlsx_doc",
    ".xls": "xlsx_doc",
    ".pdf": "pdf_doc",
}

HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)


DOC_SOURCE_TYPES = ("csv_doc", "xlsx_doc", "pdf_doc")
# A file is only promoted over the rendered page if its URL looks like pipeline content —
# avoids promoting an unrelated PDF (annual report, prescribing info) linked on the page.
PIPELINE_FILE_HINTS = (
    "pipeline", "product", "candidate", "portfolio", "development", "rd", "r-d", "clinical",
)


def rank_for_source_type(source_type: str) -> int:
    return SOURCE_RANK.get(source_type, 50)


def select_promotable_file(candidates: list[dict], page_source_type: str) -> dict | None:
    """Pick the best-ranked downloadable file (csv/xlsx/pdf) that outranks the rendered page
    AND looks like a pipeline file. The cleaner structured file becomes the authoritative
    source. Returns the candidate dict or None if the page is the best available source."""
    page_rank = rank_for_source_type(page_source_type)
    promotable = [
        c for c in candidates
        if c["source_type"] in DOC_SOURCE_TYPES
        and c["preferred_source_rank"] < page_rank
        and any(hint in c["url"].lower() for hint in PIPELINE_FILE_HINTS)
    ]
    return min(promotable, key=lambda c: c["preferred_source_rank"], default=None)


def classify_url(url: str, default: str = "pipeline_page") -> str:
    lower = url.lower().split("?", 1)[0]
    for ext, source_type in FILE_TYPE_BY_EXT.items():
        if lower.endswith(ext):
            return source_type
    if "pipeline" in lower:
        return default
    return default


def discover_from_html(base_url: str, html: str) -> list[dict]:
    candidates: list[dict] = []
    for href in HREF_RE.findall(html or ""):
        absolute = urljoin(base_url, href)
        source_type = classify_url(absolute)
        if source_type == "pipeline_page" and "pipeline" not in absolute.lower():
            continue
        candidates.append({
            "url": absolute,
            "source_type": source_type,
            "preferred_source_rank": rank_for_source_type(source_type),
        })
    unique: dict[str, dict] = {}
    for c in candidates:
        unique.setdefault(c["url"], c)
    return sorted(unique.values(), key=lambda c: c["preferred_source_rank"])


def discover_company_sources(s: Session, storage: Storage, company_query: str, persist: bool = False) -> dict:
    company = s.execute(
        select(Company).where(
            or_(
                Company.company_id == company_query,
                Company.ticker == company_query.upper(),
                Company.name.ilike(f"%{company_query}%"),
            )
        ).order_by(Company.name).limit(1)
    ).scalar_one_or_none()
    if company is None:
        return {"error": f"no company matching {company_query!r}"}

    candidates: list[dict] = []
    sources = s.execute(
        select(CompanySource).where(CompanySource.company_id == company.company_id)
    ).scalars().all()
    for src in sources:
        snap = s.execute(
            select(Snapshot)
            .where(Snapshot.source_id == src.source_id, Snapshot.html_key.isnot(None))
            .order_by(Snapshot.fetched_at.desc(), Snapshot.snapshot_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if snap and snap.html_key:
            html = storage.get(snap.html_key).decode("utf-8", errors="replace")
            candidates.extend(discover_from_html(src.url, html))

    inserted = 0
    if persist:
        existing_urls = {src.url for src in sources}
        for c in candidates:
            if c["url"] in existing_urls:
                continue
            s.add(CompanySource(
                company_id=company.company_id,
                url=c["url"],
                source_type=c["source_type"],
                preferred_source_rank=c["preferred_source_rank"],
                render_config={},
            ))
            existing_urls.add(c["url"])
            inserted += 1

    return {"company": company.name, "candidates": candidates, "inserted": inserted}
