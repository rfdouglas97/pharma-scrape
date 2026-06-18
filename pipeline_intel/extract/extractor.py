"""Silver layer: turn a rendered snapshot into structured ExtractionResult via Claude
Opus 4.8 with vision + structured outputs.

The extractor reads the snapshot's stored artifacts (visible text + full-page
screenshots) — never the live page — so extraction is reproducible and re-runnable
against the immutable bronze layer (e.g. after a prompt or model upgrade).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from sqlalchemy.orm import Session

from pipeline_intel.config import settings
from pipeline_intel.extract.client import DEFAULT_EXTRACTION_MODEL, get_client
from pipeline_intel.extract.deterministic import DETERMINISTIC_MODEL, extract_structured_pipeline
from pipeline_intel.extract.prompts import v1 as prompt
from pipeline_intel.extract.schemas import EXTRACTION_SCHEMA_VERSION, ExtractionResult
from pipeline_intel.gold.models import CompanySource, Extraction, Snapshot
from pipeline_intel.ingest.storage import Storage
from pipeline_intel.timeout import call_with_timeout

# Page text is truncated to keep input cost bounded; the screenshot remains authoritative
# for anything beyond the cutoff. Pipeline pages rarely exceed this in meaningful text.
MAX_TEXT_CHARS = 120_000
# Large pipelines (e.g. big pharma) produce big structured outputs. We stream so we can
# raise the ceiling well above the ~16K non-streaming limit without HTTP timeouts; if a
# page still hits this, the extraction is flagged needs_review (truncated).
MAX_OUTPUT_TOKENS = 64_000
TEXT_ONLY_MAX_OUTPUT_TOKENS = 8_000


@dataclass
class ExtractionOutcome:
    extraction_id: str | None
    status: str  # ok | needs_review | failed | skipped
    n_assets: int
    n_programs: int
    detail: str | None = None


def _image_block(png_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(png_bytes).decode("ascii"),
        },
    }


def build_messages(company_name: str, url: str, page_text: str, screenshots: list[bytes]) -> list[dict]:
    """Build the user turn. Each screenshot is tiled here so both the DB extraction path
    and the eval-harness path get API-safe, high-res-legible images."""
    from pipeline_intel.extract.imaging import prepare_screenshots

    text = page_text[:MAX_TEXT_CHARS]
    content: list[dict] = [
        {
            "type": "text",
            "text": prompt.USER_INSTRUCTION.format(company=company_name, url=url, page_text=text),
        }
    ]
    for shot in screenshots:
        content.extend(_image_block(t) for t in prepare_screenshots(shot))
    return [{"role": "user", "content": content}]


def _load_artifacts(s: Session, storage: Storage, snap: Snapshot) -> tuple[str, list[bytes], int]:
    text_key = (snap.render_meta or {}).get("text_key")
    page_text = storage.get(text_key).decode("utf-8", errors="replace") if text_key else ""
    screenshots = [storage.get(k) for k in (snap.screenshot_keys or [])]  # tiled in build_messages
    linked_images = [storage.get(k) for k in (snap.render_meta or {}).get("pipeline_image_keys", [])]
    return page_text, [*screenshots, *linked_images], len(linked_images)


def _load_html(storage: Storage, snap: Snapshot) -> str:
    return storage.get(snap.html_key).decode("utf-8", errors="replace") if snap.html_key else ""


def extract_snapshot(
    s: Session,
    storage: Storage,
    snapshot_id: str,
    model: str = DEFAULT_EXTRACTION_MODEL,
    include_screenshots: bool = True,
) -> ExtractionOutcome:
    snap = s.get(Snapshot, snapshot_id)
    if snap is None:
        return ExtractionOutcome(None, "failed", 0, 0, "snapshot not found")
    if snap.unchanged or not snap.html_key:
        # Unchanged snapshots carry no artifacts and need no re-extraction.
        return ExtractionOutcome(None, "skipped", 0, 0, "unchanged snapshot — nothing to extract")

    source = s.get(CompanySource, snap.source_id)
    company_name = source.company.name if source and source.company else "the company"
    url = source.url if source else ""

    page_text, screenshots, linked_image_count = _load_artifacts(s, storage, snap)
    html_text = _load_html(storage, snap)
    deterministic = extract_structured_pipeline(html_text, page_text)
    if deterministic is not None:
        ext = Extraction(
            snapshot_id=snapshot_id,
            model=DETERMINISTIC_MODEL,
            prompt_version=f"deterministic/{EXTRACTION_SCHEMA_VERSION}",
            status="ok",
            raw_json=deterministic.model_dump(),
            usage={"input_mode": "deterministic_dom"},
        )
        s.add(ext)
        s.flush()
        n_assets = len(deterministic.assets)
        n_programs = sum(len(a.programs) for a in deterministic.assets)
        return ExtractionOutcome(ext.extraction_id, "ok", n_assets, n_programs, None)

    if not include_screenshots:
        screenshots = []

    ext = Extraction(
        snapshot_id=snapshot_id,
        model=model,
        prompt_version=f"{prompt.PROMPT_VERSION}/{EXTRACTION_SCHEMA_VERSION}",
        status="ok",
        usage={
            "input_mode": _input_mode(include_screenshots, linked_image_count),
            "linked_pipeline_images": linked_image_count,
        },
    )

    try:
        result, usage, stop_reason = call_with_timeout(
            lambda: run_extraction(company_name, url, page_text, screenshots, model),
            settings().extraction_timeout_seconds,
            "extraction",
        )
    except Exception as exc:  # noqa: BLE001 — record extraction failures, never crash the run
        ext.status = "failed"
        ext.error = str(exc)
        s.add(ext)
        s.flush()
        return ExtractionOutcome(ext.extraction_id, "failed", 0, 0, str(exc))

    ext.raw_json = result.model_dump()
    ext.usage = usage
    n_assets = len(result.assets)
    n_programs = sum(len(a.programs) for a in result.assets)

    # Quality signals -> needs_review (never silently publish suspect data).
    notes = []
    if stop_reason == "max_tokens":
        notes.append("output truncated (max_tokens)")
    if result.page_notes:
        notes.append(f"model flagged: {result.page_notes}")
    if n_assets == 0:
        notes.append("no assets extracted")
    if notes:
        ext.status = "needs_review"
        ext.error = "; ".join(notes)

    s.add(ext)
    s.flush()
    return ExtractionOutcome(ext.extraction_id, ext.status, n_assets, n_programs, ext.error)


def run_extraction(
    company_name: str,
    url: str,
    page_text: str,
    screenshots: list[bytes],
    model: str = DEFAULT_EXTRACTION_MODEL,
) -> tuple[ExtractionResult, dict, str | None]:
    """Pure LLM call: messages -> validated ExtractionResult. No DB. Reused by the eval harness."""
    client = get_client()
    messages = build_messages(company_name, url, page_text, screenshots)
    if not screenshots:
        from pipeline_intel.extract.batch_api import extraction_output_config

        response = client.messages.create(
            model=model,
            max_tokens=TEXT_ONLY_MAX_OUTPUT_TOKENS,
            system=prompt.SYSTEM_PROMPT,
            messages=messages,
            output_config=extraction_output_config(),
            timeout=settings().extraction_timeout_seconds,
        )
        text = _message_text(response)
        result = ExtractionResult.model_validate_json(text)
        return result, _usage_dict(response), response.stop_reason
    with client.messages.stream(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": prompt.SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=messages,
        output_format=ExtractionResult,
    ) as stream:
        response = stream.get_final_message()
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
    }
    result = response.parsed_output
    if result is None:
        raise RuntimeError(f"structured output did not parse (stop_reason={response.stop_reason})")
    return result, usage, response.stop_reason


def _input_mode(include_screenshots: bool, linked_image_count: int) -> str:
    if not include_screenshots:
        return "text_only"
    if linked_image_count:
        return "text+vision+linked_pipeline_images"
    return "text+vision"


def _message_text(message) -> str:
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return block.text
    raise RuntimeError("message contained no text block")


def _usage_dict(message) -> dict:
    usage = message.usage
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
    }
