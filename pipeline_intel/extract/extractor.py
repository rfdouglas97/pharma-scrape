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
from pipeline_intel.extract.schemas import (
    EXTRACTION_SCHEMA_VERSION,
    VISUAL_SCHEMA_VERSION,
    ExtractionResult,
)
from pipeline_intel.extract.visual import extract_visual, resolve_visual_model
from pipeline_intel.gold.models import CompanySource, Extraction, Snapshot
from pipeline_intel.ingest.classify import phase_hits
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
# A page with this many phase-vocabulary hits in its visible text carries the pipeline in
# the DOM, not an image — extract from text and skip the slow full-page screenshot vision pass.
_TEXT_RICH_PHASE_HITS = 12


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


def is_text_rich(page_text: str, is_document: bool, linked_image_count: int) -> bool:
    """True when the pipeline lives in the DOM text (dense phase vocabulary, no linked chart):
    extract from text and skip the slow full-page vision pass. Documents have their own path."""
    return (
        not is_document
        and not linked_image_count
        and phase_hits(page_text) >= _TEXT_RICH_PHASE_HITS
    )


def _should_use_visual(source: CompanySource | None, page_text: str, linked_image_count: int) -> bool:
    """Route to the dedicated two-pass visual extractor when the pipeline is image-backed:
    the source is classified `image_page`, or it links a pipeline image and the rendered text
    carries little phase vocabulary (the data lives in the chart, not the DOM)."""
    if source is not None and source.source_type == "image_page":
        return True
    return linked_image_count > 0 and phase_hits(page_text) < 3


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
    meta = snap.render_meta or {}
    is_document = meta.get("source_kind") == "document"
    has_artifact = bool(snap.html_key or meta.get("text_key") or snap.screenshot_keys)
    if snap.unchanged or not has_artifact:
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

    # Image-backed pipelines (phase-bar charts) go through the dedicated two-pass visual
    # extractor — generic vision degrades phase/target/modality on these.
    if include_screenshots and screenshots and _should_use_visual(source, page_text, linked_image_count):
        visual_model = resolve_visual_model(model)
        ext = Extraction(
            snapshot_id=snapshot_id,
            model=visual_model,
            prompt_version=f"visual/{VISUAL_SCHEMA_VERSION}",
            status="ok",
            usage={"input_mode": "visual_two_pass", "linked_pipeline_images": linked_image_count},
        )
        try:
            outcome = call_with_timeout(
                lambda: extract_visual(company_name, url, screenshots, visual_model),
                settings().extraction_timeout_seconds,
                "visual_extraction",
            )
        except Exception as exc:  # noqa: BLE001 — record failures, never crash the run
            ext.status = "failed"
            ext.error = str(exc)
            s.add(ext)
            s.flush()
            return ExtractionOutcome(ext.extraction_id, "failed", 0, 0, str(exc))

        ext.raw_json = outcome.result.model_dump()
        ext.usage = {**ext.usage, **outcome.usage, "visual_transcription": outcome.transcription.model_dump()}
        n_assets = len(outcome.result.assets)
        n_programs = sum(len(a.programs) for a in outcome.result.assets)
        notes = []
        if outcome.stop_reason == "max_tokens":
            notes.append("output truncated (max_tokens)")
        if n_assets == 0:
            notes.append("no rows transcribed")
        low_conf = sum(1 for r in outcome.transcription.rows if r.confidence < 0.5)
        if low_conf:
            notes.append(f"{low_conf} low-confidence rows")
        if notes:
            ext.status = "needs_review"
            ext.error = "; ".join(notes)
        s.add(ext)
        s.flush()
        return ExtractionOutcome(ext.extraction_id, ext.status, n_assets, n_programs, ext.error)

    # Text-rich pages carry the pipeline in their DOM text, not an image: extract from text
    # via the high-ceiling streaming path and skip the slow full-page vision pass (what timed
    # out big table pages like AstraZeneca/Merck/Roche).
    text_rich = is_text_rich(page_text, is_document, linked_image_count)
    if text_rich:
        screenshots = []
    large = is_document or text_rich

    if is_document:
        input_mode = "document"
    elif text_rich:
        input_mode = "text_dom"
    else:
        input_mode = _input_mode(include_screenshots, linked_image_count)
    ext = Extraction(
        snapshot_id=snapshot_id,
        model=model,
        prompt_version=f"{prompt.PROMPT_VERSION}/{EXTRACTION_SCHEMA_VERSION}",
        status="ok",
        usage={
            "input_mode": input_mode,
            "linked_pipeline_images": linked_image_count,
        },
    )

    try:
        result, usage, stop_reason = call_with_timeout(
            lambda: run_extraction(company_name, url, page_text, screenshots, model, large=large),
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
    # Preserve the input_mode/linked-image tags set at creation; merge in API token usage.
    ext.usage = {**(ext.usage or {}), **usage}
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
    large: bool = False,
) -> tuple[ExtractionResult, dict, str | None]:
    """Pure LLM call: messages -> validated ExtractionResult. No DB. Reused by the eval harness.

    `large` forces the high-ceiling streaming path even with no screenshots — used for
    document sources (parsed spreadsheets/PDFs) whose pipelines can exceed the cheap
    text-only output budget."""
    client = get_client()
    messages = build_messages(company_name, url, page_text, screenshots)
    if not screenshots and not large:
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
    # Documents (large text, no screenshots) are clean structured tables -> transcription, not
    # reasoning. Skipping adaptive thinking roughly halves latency and avoids timeouts on big
    # pipelines; vision pages keep thinking for chart/phase-bar interpretation.
    stream_kwargs: dict = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": [
            {
                "type": "text",
                "text": prompt.SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": messages,
        "output_format": ExtractionResult,
    }
    if screenshots:
        stream_kwargs["thinking"] = {"type": "adaptive"}
    with client.messages.stream(**stream_kwargs) as stream:
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
