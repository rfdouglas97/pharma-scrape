"""Two-pass visual extractor for image-backed pipeline charts.

Pass 1 (LLM vision): transcribe the chart row-by-row into a VisualTranscription — phase-axis
columns + one VisualEvidenceRow per program, each recording how its phase was read off the
bar. Pass 2 (deterministic): normalize that transcription into the canonical ExtractionResult.

Splitting transcription from normalization keeps the hard part (reading phase from bar
geometry) auditable and lets a QA judge compare the final JSON back to the evidence + image.
No company-specific code: a phase-bar chart is read as a phase-bar chart regardless of company.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from pipeline_intel.extract.client import DEFAULT_EXTRACTION_MODEL, OPUS_MODEL, get_client
from pipeline_intel.extract.imaging import prepare_screenshots
from pipeline_intel.extract.prompts import visual_v1 as vprompt
from pipeline_intel.extract.schemas import (
    ExtractedAsset,
    ExtractedProgram,
    ExtractionResult,
    VisualTranscription,
)

# Phase-bar reasoning is the hardest extraction task -> default to the strongest model.
VISUAL_MODEL = OPUS_MODEL
VISUAL_MAX_OUTPUT_TOKENS = 32_000


@dataclass
class VisualOutcome:
    result: ExtractionResult
    transcription: VisualTranscription
    usage: dict
    stop_reason: str | None


def resolve_visual_model(model: str | None) -> str:
    """Use Opus for the visual transcription unless an explicit non-default model is given."""
    return VISUAL_MODEL if (model is None or model == DEFAULT_EXTRACTION_MODEL) else model


def _image_block(png_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(png_bytes).decode("ascii"),
        },
    }


def transcribe_chart(
    company_name: str, url: str, images: list[bytes], model: str = VISUAL_MODEL
) -> tuple[VisualTranscription, dict, str | None]:
    """Pass 1: read the chart image(s) into a structured VisualTranscription."""
    client = get_client()
    content: list[dict] = [
        {"type": "text", "text": vprompt.VISUAL_USER_INSTRUCTION.format(company=company_name, url=url)}
    ]
    for img in images:
        content.extend(_image_block(t) for t in prepare_screenshots(img))
    with client.messages.stream(
        model=model,
        max_tokens=VISUAL_MAX_OUTPUT_TOKENS,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": vprompt.VISUAL_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": content}],
        output_format=VisualTranscription,
    ) as stream:
        response = stream.get_final_message()
    transcription = response.parsed_output
    if transcription is None:
        raise RuntimeError(f"visual transcription did not parse (stop_reason={response.stop_reason})")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
    }
    return transcription, usage, response.stop_reason


def normalize_transcription(transcription: VisualTranscription) -> ExtractionResult:
    """Pass 2 (deterministic): VisualTranscription -> ExtractionResult. Rows sharing an asset
    name collapse into one asset with multiple programs; target/modality fill from the first
    row that discloses them."""
    by_name: dict[str, ExtractedAsset] = {}
    order: list[str] = []
    for row in transcription.rows:
        key = row.asset_name.strip()
        program = ExtractedProgram(
            indication_verbatim=row.indication or row.asset_name,
            phase_verbatim=row.phase,
            status=row.status,
        )
        asset = by_name.get(key)
        if asset is None:
            by_name[key] = ExtractedAsset(
                preferred_name=row.asset_name,
                modality_verbatim=row.modality,
                target_verbatim=row.target,
                programs=[program],
            )
            order.append(key)
        else:
            asset.programs.append(program)
            if asset.target_verbatim is None:
                asset.target_verbatim = row.target
            if asset.modality_verbatim is None:
                asset.modality_verbatim = row.modality

    notes = "pipeline transcribed from chart image"
    if transcription.chart_notes:
        notes = f"{notes}; {transcription.chart_notes}"
    return ExtractionResult(assets=[by_name[k] for k in order], page_notes=notes)


def extract_visual(
    company_name: str, url: str, images: list[bytes], model: str = VISUAL_MODEL
) -> VisualOutcome:
    transcription, usage, stop_reason = transcribe_chart(company_name, url, images, model)
    result = normalize_transcription(transcription)
    return VisualOutcome(result=result, transcription=transcription, usage=usage, stop_reason=stop_reason)
