"""Autonomous extraction QA.

The checker combines cheap deterministic safeguards with an LLM-as-judge verdict. It is
designed to gate gold publication without asking a human to inspect every company.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline_intel.config import settings
from pipeline_intel.extract.client import OPUS_MODEL, SONNET_MODEL
from pipeline_intel.extract.deterministic import DETERMINISTIC_MODEL
from pipeline_intel.extract.schemas import ExtractionResult
from pipeline_intel.gold.models import CompanySource, Extraction, QAReport, Snapshot
from pipeline_intel.ingest.storage import Storage
from pipeline_intel.timeout import call_with_timeout

QA_JUDGE_MODEL = SONNET_MODEL
# Image-backed pipelines need a vision judge that re-reads the chart; bar-position phase
# checks want the strongest model.
VISUAL_QA_MODEL = OPUS_MODEL
Verdict = Literal["pass", "warn", "fail"]

COUNT_PATTERNS = (
    re.compile(r"\b(?:pipeline|portfolio)\s+(?:includes|contains|comprises|has)\s+(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})\s+(?:assets|programs|projects|medicines|molecules|compounds)\b", re.I),
    re.compile(
        r"\btotal(?:\s+of)?\s+(\d{1,3})\s+"
        r"(?:assets|programs|projects|medicines|molecules|compounds)\b",
        re.I,
    ),
)


class CountMismatch(BaseModel):
    label: str
    expected: int | None = None
    observed: int | None = None
    detail: str


class QAVerdict(BaseModel):
    verdict: Verdict = Field(description="pass, warn, or fail")
    confidence: float = Field(ge=0, le=1)
    missing_assets: list[str] = Field(default_factory=list)
    extra_assets: list[str] = Field(default_factory=list)
    suspicious_fields: list[str] = Field(default_factory=list)
    count_mismatches: list[CountMismatch] = Field(default_factory=list)
    recommended_action: str | None = None
    rationale: str | None = None


@dataclass
class QAOutcome:
    qa_report_id: str | None
    extraction_id: str
    verdict: Verdict
    confidence: float
    expected_count: int | None
    observed_count: int
    recommended_action: str | None = None

    def as_dict(self) -> dict:
        return {
            "qa_report_id": self.qa_report_id,
            "extraction_id": self.extraction_id,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "expected_count": self.expected_count,
            "observed_count": self.observed_count,
            "recommended_action": self.recommended_action,
        }


def observed_asset_count(result: ExtractionResult) -> int:
    return len(result.assets)


def observed_program_count(result: ExtractionResult) -> int:
    return sum(len(asset.programs) for asset in result.assets)


def infer_expected_count(page_text: str, known_expected_count: int | None = None) -> int | None:
    if known_expected_count is not None:
        return known_expected_count
    candidates: list[int] = []
    for pat in COUNT_PATTERNS:
        candidates.extend(int(m.group(1)) for m in pat.finditer(page_text or ""))
    plausible = [n for n in candidates if 1 <= n <= 250]
    return min(plausible) if plausible else None


def deterministic_verdict(
    result: ExtractionResult,
    page_text: str,
    known_expected_count: int | None = None,
    previous_observed_count: int | None = None,
) -> tuple[QAVerdict, int | None, int]:
    expected = infer_expected_count(page_text, known_expected_count)
    is_known_count = known_expected_count is not None
    observed = observed_asset_count(result)
    observed_programs = observed_program_count(result)
    mismatches: list[CountMismatch] = []
    verdict: Verdict = "pass"
    confidence = 0.82
    action: str | None = None

    if observed == 0:
        verdict = "fail"
        confidence = 0.98
        mismatches.append(CountMismatch(
            label="asset_count", expected=expected, observed=observed,
            detail="No assets were extracted from a changed snapshot.",
        ))
        action = "rerender_and_reextract"
    elif expected is not None:
        # A page's stated count may be assets OR programs/projects ("186 projects",
        # "50 medicines"). Reconcile against whichever count is closer so we don't fail a
        # correct extraction just because the company counts programs and we counted assets.
        best = min((observed, observed_programs), key=lambda o: abs(expected - o))
        delta = abs(expected - best)
        tolerance = max(2, round(expected * 0.1))
        detail_counts = f"(assets={observed}, programs={observed_programs}, expected={expected})"
        if delta > tolerance and is_known_count:
            # Only a TRUSTED registry count hard-fails. Counts scraped from page/PDF text are
            # too noisy to block on (they pick up per-phase subtotals like "7 NMEs").
            verdict = "fail"
            confidence = 0.95
            mismatches.append(CountMismatch(
                label="asset_or_program_count", expected=expected, observed=best,
                detail=f"Neither asset nor program count is within tolerance of known total {detail_counts}.",
            ))
            action = "focused_reextract_missing_sections"
        elif delta > tolerance:
            verdict = "warn"
            confidence = 0.80
            mismatches.append(CountMismatch(
                label="asset_or_program_count", expected=expected, observed=best,
                detail=f"Inferred page count differs from extraction (soft signal) {detail_counts}.",
            ))
        elif delta:
            verdict = "warn"
            confidence = 0.88
            mismatches.append(CountMismatch(
                label="asset_or_program_count", expected=expected, observed=best,
                detail=f"Count is close but not identical to the page total {detail_counts}.",
            ))

    if previous_observed_count and previous_observed_count >= 5:
        drop_ratio = 1 - (observed / previous_observed_count)
        if drop_ratio > 0.4:
            verdict = "fail"
            confidence = max(confidence, 0.93)
            mismatches.append(CountMismatch(
                label="previous_count_drop", expected=previous_observed_count, observed=observed,
                detail="Observed assets dropped more than 40% from the prior successful scrape.",
            ))
            action = "rerender_with_repair_config"

    return QAVerdict(
        verdict=verdict,
        confidence=confidence,
        count_mismatches=mismatches,
        recommended_action=action,
        rationale="Deterministic preflight checks.",
    ), expected, observed


def merge_verdicts(preflight: QAVerdict, judge: QAVerdict | None) -> QAVerdict:
    if judge is None:
        return preflight
    severity = {"pass": 0, "warn": 1, "fail": 2}
    verdict = preflight.verdict if severity[preflight.verdict] >= severity[judge.verdict] else judge.verdict
    return QAVerdict(
        verdict=verdict,
        confidence=max(preflight.confidence, judge.confidence),
        missing_assets=[*preflight.missing_assets, *judge.missing_assets],
        extra_assets=[*preflight.extra_assets, *judge.extra_assets],
        suspicious_fields=[*preflight.suspicious_fields, *judge.suspicious_fields],
        count_mismatches=[*preflight.count_mismatches, *judge.count_mismatches],
        recommended_action=preflight.recommended_action or judge.recommended_action,
        rationale=judge.rationale or preflight.rationale,
    )


def llm_judge(
    company_name: str,
    source_url: str,
    page_text: str,
    extraction: ExtractionResult,
    model: str = QA_JUDGE_MODEL,
) -> QAVerdict:
    from pipeline_intel.extract.client import get_client

    client = get_client()
    prompt = (
        "You are a pharma pipeline data QA judge. Compare the official source evidence "
        "against the structured extraction. Decide whether the extraction is faithful enough "
        "to publish without human review. Be strict about missing assets, active vs removed/"
        "discontinued sections, phases, indications, targets/MoA, modalities, and partners.\n\n"
        f"Company: {company_name}\nSource: {source_url}\n\n"
        f"Visible source text:\n{page_text[:100000]}\n\n"
        f"Extraction JSON:\n{extraction.model_dump_json()}"
    )
    with client.messages.stream(
        model=model,
        max_tokens=4096,
        system="Return only the structured QA verdict.",
        messages=[{"role": "user", "content": prompt}],
        output_format=QAVerdict,
    ) as stream:
        response = stream.get_final_message()
    if response.parsed_output is None:
        raise RuntimeError("QA judge did not return a parseable verdict")
    return response.parsed_output


def visual_judge(
    company_name: str,
    source_url: str,
    images: list[bytes],
    extraction: ExtractionResult,
    transcription: dict | None = None,
    model: str = VISUAL_QA_MODEL,
) -> QAVerdict:
    """Vision QA for image-backed pipelines: re-read the chart image and check the extraction
    against it. The phase read from bar geometry is the high-risk field, so the judge is told
    to scrutinise each row's phase vs where its bar actually ends."""
    import base64

    from pipeline_intel.extract.client import get_client
    from pipeline_intel.extract.imaging import prepare_screenshots

    client = get_client()
    instruction = (
        "You are a pharma pipeline QA judge for an IMAGE-BASED pipeline chart. The chart "
        "image(s) are the source of truth. Check the structured extraction against the chart:\n"
        "- Every program row present (no missing/extra rows).\n"
        "- Each program's PHASE matches where its bar actually ends relative to the phase-axis "
        "column headers — this is the most error-prone field; flag any row whose phase looks "
        "off by a column in suspicious_fields (name the asset + the phase you read).\n"
        "- Target/payload, indication, and status match the chart.\n"
        "Set verdict=fail for missing rows or clearly wrong phases; warn for borderline bar "
        "positions; pass if faithful.\n\n"
        f"Company: {company_name}\nSource: {source_url}\n\n"
        f"Extraction JSON:\n{extraction.model_dump_json()}"
    )
    if transcription:
        import json

        rows_json = json.dumps(transcription)[:8000]
        instruction += f"\n\nThe extractor's own row-by-row reading (for reference):\n{rows_json}"

    content: list[dict] = [{"type": "text", "text": instruction}]
    for img in images:
        for tile in prepare_screenshots(img):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(tile).decode("ascii"),
                },
            })
    with client.messages.stream(
        model=model,
        max_tokens=4096,
        system="Return only the structured QA verdict.",
        messages=[{"role": "user", "content": content}],
        output_format=QAVerdict,
    ) as stream:
        response = stream.get_final_message()
    if response.parsed_output is None:
        raise RuntimeError("visual QA judge did not return a parseable verdict")
    return response.parsed_output


def _load_images(storage: Storage, snap: Snapshot) -> list[bytes]:
    keys = list((snap.render_meta or {}).get("pipeline_image_keys", []))
    keys += list(snap.screenshot_keys or [])
    return [storage.get(k) for k in keys]


def latest_successful_observed_count(s: Session, source_id: str, before_extraction_id: str) -> int | None:
    current = s.get(Extraction, before_extraction_id)
    if current is None:
        return None
    rows = s.execute(
        select(Extraction.observed_count)
        .join(Snapshot, Snapshot.snapshot_id == Extraction.snapshot_id)
        .where(
            Snapshot.source_id == source_id,
            Extraction.extraction_id != before_extraction_id,
            Extraction.qa_status.in_(("pass", "warn")),
            Extraction.observed_count.isnot(None),
        )
        .order_by(Extraction.extracted_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return rows


def _load_page_text(storage: Storage, snap: Snapshot) -> str:
    text_key = (snap.render_meta or {}).get("text_key")
    return storage.get(text_key).decode("utf-8", errors="replace") if text_key else ""


def run_quality_check(
    s: Session,
    storage: Storage,
    extraction_id: str,
    judge=None,
    model: str = QA_JUDGE_MODEL,
) -> QAOutcome:
    ext = s.get(Extraction, extraction_id)
    if ext is None or not ext.raw_json:
        raise ValueError(f"no extraction with raw_json for {extraction_id!r}")
    snap = s.get(Snapshot, ext.snapshot_id)
    source = s.get(CompanySource, snap.source_id) if snap else None
    if snap is None or source is None:
        raise ValueError(f"snapshot/source missing for extraction {extraction_id!r}")

    result = ExtractionResult.model_validate(ext.raw_json)
    page_text = _load_page_text(storage, snap)
    previous_count = latest_successful_observed_count(s, source.source_id, extraction_id)
    preflight, expected, observed = deterministic_verdict(
        result,
        page_text,
        known_expected_count=source.known_expected_count,
        previous_observed_count=previous_count,
    )

    is_visual = (ext.usage or {}).get("input_mode") == "visual_two_pass"
    judge_verdict = None
    if ext.model == DETERMINISTIC_MODEL:
        judge_verdict = QAVerdict(
            verdict="pass",
            confidence=0.99,
            rationale="Deterministic structured DOM extraction; no LLM judge required.",
        )
    elif judge is not None:
        judge_verdict = judge(source.company.name, source.url, page_text, result)
    elif is_visual and preflight.verdict != "fail":
        # Image pipelines have little/no page text — re-read the chart image instead.
        images = _load_images(storage, snap)
        transcription = (ext.usage or {}).get("visual_transcription")
        if images:
            judge_verdict = call_with_timeout(
                lambda: visual_judge(
                    source.company.name, source.url, images, result, transcription, model=VISUAL_QA_MODEL
                ),
                settings().qa_timeout_seconds,
                "visual qa judge",
            )
    elif preflight.verdict != "fail":
        judge_verdict = call_with_timeout(
            lambda: llm_judge(source.company.name, source.url, page_text, result, model=model),
            settings().qa_timeout_seconds,
            "qa judge",
        )

    verdict = merge_verdicts(preflight, judge_verdict)
    report_payload = verdict.model_dump()
    qa = QAReport(
        extraction_id=extraction_id,
        model=model,
        verdict=verdict.verdict,
        confidence=Decimal(str(round(verdict.confidence, 3))),
        expected_count=expected,
        observed_count=observed,
        missing_assets=verdict.missing_assets,
        extra_assets=verdict.extra_assets,
        suspicious_fields=verdict.suspicious_fields,
        count_mismatches=[m.model_dump() for m in verdict.count_mismatches],
        recommended_action=verdict.recommended_action,
        report=report_payload,
    )
    s.add(qa)
    s.flush()

    ext.qa_status = verdict.verdict
    ext.qa_confidence = Decimal(str(round(verdict.confidence, 3)))
    ext.qa_report = report_payload
    ext.expected_count = expected
    ext.observed_count = observed
    if source.company:
        source.company.pipeline_status = (
            "qa_passed" if verdict.verdict in ("pass", "warn") else "needs_repair"
        )

    return QAOutcome(
        qa_report_id=qa.qa_report_id,
        extraction_id=extraction_id,
        verdict=verdict.verdict,
        confidence=verdict.confidence,
        expected_count=expected,
        observed_count=observed,
        recommended_action=verdict.recommended_action,
    )
