"""Anthropic Message Batch support for extraction.

This module submits already-rendered snapshots to the asynchronous Batches API and
later collects results into the existing silver `extraction` table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import ValidationError
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from pipeline_intel.extract.client import DEFAULT_EXTRACTION_MODEL, get_client
from pipeline_intel.extract.extractor import MAX_OUTPUT_TOKENS, _load_artifacts, build_messages
from pipeline_intel.extract.prompts import v1 as prompt
from pipeline_intel.extract.schemas import EXTRACTION_SCHEMA_VERSION, ExtractionResult
from pipeline_intel.gold.models import Company, CompanySource, Extraction, ModelBatch, Snapshot
from pipeline_intel.ingest.storage import Storage
from pipeline_intel.model_routing import ModelRoute, route_for_company_source


def extraction_output_config() -> dict:
    return {
        "format": {
            "type": "json_schema",
            "schema": _strict_json_schema(ExtractionResult.model_json_schema()),
        }
    }


def _strict_json_schema(schema: dict) -> dict:
    """Anthropic batch structured output requires closed object schemas."""
    if isinstance(schema, dict):
        out = {k: _strict_json_schema(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [_strict_json_schema(v) for v in schema]
    return schema


def pending_snapshots(
    s: Session,
    limit: int = 10,
    company_query: str | None = None,
) -> list[Snapshot]:
    already_extracted = exists().where(
        and_(
            Extraction.snapshot_id == Snapshot.snapshot_id,
            Extraction.status.in_(("ok", "needs_review", "batch_submitted")),
        )
    )
    stmt = (
        select(Snapshot)
        .join(CompanySource, CompanySource.source_id == Snapshot.source_id)
        .join(Company, Company.company_id == CompanySource.company_id)
        .where(
            Snapshot.unchanged.is_(False),
            Snapshot.html_key.isnot(None),
            ~already_extracted,
        )
        .order_by(Snapshot.fetched_at.desc(), Snapshot.snapshot_id.desc())
    )
    if company_query:
        stmt = stmt.where(
            or_(
                Company.company_id == company_query,
                Company.ticker == company_query.upper(),
                Company.name.ilike(f"%{company_query}%"),
            )
        )
    return list(s.execute(stmt.limit(limit)).scalars())


def build_extraction_request(
    s: Session,
    storage: Storage,
    snap: Snapshot,
    route: ModelRoute | None = None,
) -> tuple[Request, Extraction, dict]:
    source = s.get(CompanySource, snap.source_id)
    if source is None or source.company is None:
        raise ValueError(f"snapshot {snap.snapshot_id} has no source/company")
    route = route or route_for_company_source(source.company, source)
    page_text, screenshots, linked_image_count = _load_artifacts(s, storage, snap)
    messages = build_messages(source.company.name, source.url, page_text, screenshots)
    ext = Extraction(
        snapshot_id=snap.snapshot_id,
        model=route.extraction_model,
        prompt_version=f"{prompt.PROMPT_VERSION}/{EXTRACTION_SCHEMA_VERSION}",
        status="batch_submitted",
        usage={"linked_pipeline_images": linked_image_count},
        qa_report={},
    )
    s.add(ext)
    s.flush()
    custom_id = f"ext_{ext.extraction_id}"
    params = MessageCreateParamsNonStreaming(
        model=route.extraction_model or DEFAULT_EXTRACTION_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        thinking={"type": "adaptive"},
        system=prompt.SYSTEM_PROMPT,
        messages=messages,
        output_config=extraction_output_config(),
    )
    item = {
        "custom_id": custom_id,
        "snapshot_id": snap.snapshot_id,
        "extraction_id": ext.extraction_id,
        "company": source.company.name,
        "url": source.url,
        "route": route.as_dict(),
    }
    return Request(custom_id=custom_id, params=params), ext, item


def submit_extraction_batch(
    s: Session,
    storage: Storage,
    limit: int = 10,
    company: str | None = None,
) -> dict:
    snaps = pending_snapshots(s, limit=limit, company_query=company)
    requests: list[Request] = []
    items: dict[str, dict] = {}
    skipped: list[dict] = []
    for snap in snaps:
        try:
            req, _ext, item = build_extraction_request(s, storage, snap)
        except FileNotFoundError as exc:
            skipped.append({"snapshot_id": snap.snapshot_id, "reason": str(exc)})
            continue
        requests.append(req)
        items[item["custom_id"]] = item
    if not requests:
        return {"submitted": 0, "provider_batch_id": None, "items": {}, "skipped": skipped}

    client = get_client()
    provider_batch = client.messages.batches.create(requests=requests)
    provider_payload = _model_dump(provider_batch)
    row = ModelBatch(
        provider_batch_id=provider_batch.id,
        kind="extraction",
        status=provider_batch.processing_status,
        request_count=len(requests),
        items=items,
        provider_response=provider_payload,
    )
    s.add(row)
    s.flush()
    return {
        "submitted": len(requests),
        "model_batch_id": row.model_batch_id,
        "provider_batch_id": provider_batch.id,
        "status": provider_batch.processing_status,
        "items": items,
        "skipped": skipped,
    }


def refresh_batch_status(s: Session, provider_batch_id: str) -> dict:
    row = _get_batch_row(s, provider_batch_id)
    client = get_client()
    provider_batch = client.messages.batches.retrieve(row.provider_batch_id)
    row.status = provider_batch.processing_status
    row.provider_response = _model_dump(provider_batch)
    if provider_batch.processing_status == "ended":
        row.completed_at = datetime.now(UTC)
    return {
        "model_batch_id": row.model_batch_id,
        "provider_batch_id": row.provider_batch_id,
        "status": row.status,
        "request_count": row.request_count,
        "provider_response": row.provider_response,
    }


def collect_extraction_batch(s: Session, provider_batch_id: str) -> dict:
    row = _get_batch_row(s, provider_batch_id)
    if row.status != "ended":
        refresh_batch_status(s, provider_batch_id)
    client = get_client()
    collected = succeeded = failed = 0
    errors: list[dict] = []

    for result in client.messages.batches.results(row.provider_batch_id):
        collected += 1
        item = (row.items or {}).get(result.custom_id, {})
        ext_id = item.get("extraction_id")
        ext = s.get(Extraction, ext_id) if ext_id else None
        if ext is None:
            errors.append({"custom_id": result.custom_id, "error": "missing extraction row"})
            failed += 1
            continue
        if result.result.type != "succeeded":
            ext.status = "failed"
            ext.error = json.dumps(_model_dump(result.result))
            failed += 1
            continue

        message = result.result.message
        text = _message_text(message)
        try:
            parsed = ExtractionResult.model_validate_json(text)
        except ValidationError as exc:
            ext.status = "failed"
            ext.error = f"batch structured output did not validate: {exc}"
            failed += 1
            continue

        ext.raw_json = parsed.model_dump()
        ext.usage = _usage_dict(message)
        ext.status = "ok"
        ext.error = None
        if getattr(message, "stop_reason", None) == "max_tokens":
            ext.status = "needs_review"
            ext.error = "output truncated (max_tokens)"
        if parsed.page_notes:
            ext.status = "needs_review"
            ext.error = f"model flagged: {parsed.page_notes}"
        if not parsed.assets:
            ext.status = "needs_review"
            ext.error = "no assets extracted"
        succeeded += 1

    row.status = "collected"
    row.completed_at = datetime.now(UTC)
    return {
        "model_batch_id": row.model_batch_id,
        "provider_batch_id": row.provider_batch_id,
        "collected": collected,
        "succeeded": succeeded,
        "failed": failed,
        "errors": errors,
    }


def _get_batch_row(s: Session, provider_batch_id: str) -> ModelBatch:
    row = s.execute(
        select(ModelBatch).where(
            or_(
                ModelBatch.provider_batch_id == provider_batch_id,
                ModelBatch.model_batch_id == provider_batch_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"no model batch matching {provider_batch_id!r}")
    return row


def _message_text(message) -> str:
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError("batch message contained no text block")


def _usage_dict(message) -> dict:
    usage = getattr(message, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
        "batch_discount": True,
    }


def _model_dump(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)
