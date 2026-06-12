"""FastAPI read + review service — a thin wrapper over pipeline_intel.search and the
golden-fixture helpers. This is the shared service layer the UI (and later API product /
MCP) sit on. Read-only for the dataset; the only writes are golden labels from review.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, select

from pipeline_intel.db import session
from pipeline_intel.gold.models import (
    Company,
    CompanySource,
    Extraction,
    Snapshot,
)
from pipeline_intel.history import distribution as history
from pipeline_intel.ingest.storage import get_storage
from pipeline_intel.search import facets

app = FastAPI(title="Pharma Pipeline Intelligence API", version="0.1.0")

# Local dev: allow the Next.js dev server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/v1/health")
def health() -> dict:
    return {"ok": True}


# --- Browse -----------------------------------------------------------------
@app.get("/v1/companies")
def companies() -> list[dict]:
    with session() as s:
        return facets.list_companies(s)


@app.get("/v1/companies/{company_id}")
def company(company_id: str) -> dict:
    with session() as s:
        c = facets.get_company(s, company_id)
    if c is None:
        raise HTTPException(404, "company not found")
    return c


# --- History (longitudinal change feed + composition over time) --------------
@app.get("/v1/companies/{company_id}/history/distribution")
def history_distribution(company_id: str, dim: str = "phase") -> dict:
    """Per-quarter PROGRAM composition. dim=phase | therapeutic_area. Quarantined quarters flagged."""
    if dim not in ("phase", "therapeutic_area"):
        raise HTTPException(400, "dim must be 'phase' or 'therapeutic_area'")
    with session() as s:
        return history.period_distribution(s, company_id, dim)


@app.get("/v1/companies/{company_id}/history/events")
def history_events(company_id: str, types: str | None = None, status: str | None = "confirmed") -> dict:
    """Change-event feed. `types` = comma-separated event types; `status`='confirmed' (default) or ''."""
    type_list = [t for t in (types or "").split(",") if t] or None
    with session() as s:
        rows = history.change_events(s, company_id, types=type_list, status=status or None)
    return {"company_id": company_id, "count": len(rows), "events": rows}


@app.get("/v1/companies/{company_id}/history/summary")
def history_summary(company_id: str) -> dict:
    """Headline metrics: pipeline size, additions/exits per quarter (exits split by class), advances."""
    with session() as s:
        return history.history_summary(s, company_id)


@app.get("/v1/programs")
def programs(
    q: str | None = None,
    phase: str | None = None,
    modality: str | None = None,
    company_id: str | None = None,
    status: str | None = None,
    therapeutic_area: str | None = None,
    active_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    with session() as s:
        return facets.search_programs(
            s, q=q, phase=phase, modality=modality, company_id=company_id,
            status=status, therapeutic_area=therapeutic_area,
            active_only=active_only, limit=min(limit, 500), offset=offset,
        )


@app.get("/v1/assets/{asset_id}")
def asset(asset_id: str) -> dict:
    with session() as s:
        a = facets.get_asset(s, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    return a


@app.get("/v1/facets")
def facet_values() -> dict:
    with session() as s:
        return facets.facet_values(s)


@app.get("/v1/meta/coverage")
def coverage() -> list[dict]:
    with session() as s:
        return facets.coverage(s)


@app.get("/v1/indications")
def indications(q: str | None = None, mapped_only: bool = True) -> list[dict]:
    with session() as s:
        return facets.list_indications(s, q=q, mapped_only=mapped_only)


@app.get("/v1/indications/adjacent")
def indication_adjacent(
    curie: str, max_up: int = 2, active_only: bool = True
) -> dict:
    """Biology-aware search: programs in this disease + adjacent (sub/super-type) indications."""
    with session() as s:
        return facets.programs_by_indication(s, curie, max_ancestor_hops=max_up, active_only=active_only)


# --- Provenance: serve the raw source screenshot ----------------------------
@app.get("/v1/snapshots/{snapshot_id}/screenshot")
def screenshot(snapshot_id: str) -> Response:
    with session() as s:
        snap = s.get(Snapshot, snapshot_id)
        if snap is None or not snap.screenshot_keys:
            raise HTTPException(404, "no screenshot for snapshot")
        key = snap.screenshot_keys[0]
    data = get_storage().get(key)
    return Response(content=data, media_type="image/png")


# --- Review / labeling (unblocks the eval gate) -----------------------------
@app.get("/v1/review/extractions")
def list_extractions() -> list[dict]:
    """Latest extraction per snapshot, with company + screenshot pointer, for the review UI."""
    with session() as s:
        rows = s.execute(
            select(
                Extraction.extraction_id, Extraction.snapshot_id, Extraction.status,
                Extraction.extracted_at, Company.name.label("company"),
            )
            .join(Snapshot, Snapshot.snapshot_id == Extraction.snapshot_id)
            .join(CompanySource, CompanySource.source_id == Snapshot.source_id)
            .join(Company, Company.company_id == CompanySource.company_id)
            .where(Extraction.raw_json.isnot(None))
            .order_by(desc(Extraction.extracted_at))
        ).all()
        seen, out = set(), []
        for r in rows:
            if r.snapshot_id in seen:
                continue
            seen.add(r.snapshot_id)
            out.append(dict(r._mapping))
        return out


@app.get("/v1/review/extractions/{extraction_id}")
def get_extraction(extraction_id: str) -> dict:
    with session() as s:
        ext = s.get(Extraction, extraction_id)
        if ext is None:
            raise HTTPException(404, "extraction not found")
        snap = s.get(Snapshot, ext.snapshot_id)
        source = s.get(CompanySource, snap.source_id) if snap else None
        company = s.get(Company, source.company_id) if source else None
        return {
            "extraction_id": ext.extraction_id,
            "snapshot_id": ext.snapshot_id,
            "status": ext.status,
            "model": ext.model,
            "company": company.name if company else "",
            "url": source.url if source else "",
            "extraction": ext.raw_json,
        }


class GoldenSave(BaseModel):
    snapshot_id: str
    corrected: dict
    format: str = "unknown"


@app.post("/v1/review/golden")
def save_golden(body: GoldenSave) -> dict:
    from pipeline_intel.quality.golden import save_golden_label

    result = save_golden_label(body.snapshot_id, body.corrected, body.format)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result
