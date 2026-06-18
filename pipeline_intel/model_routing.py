"""Cost-aware model routing for extraction and QA.

The policy is intentionally simple and visible: Sonnet is the normal workhorse, Haiku is
used only for genuinely small/simple extractions, and Opus is reserved for complex sources
or appellate QA after a cheaper model cannot establish confidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline_intel.extract.client import HAIKU_MODEL, OPUS_MODEL, SONNET_MODEL
from pipeline_intel.gold.models import Company, CompanySource

HIGH_VALUE_TICKERS = {
    "ABBV", "AMGN", "AZN", "BMY", "GILD", "GSK", "JNJ", "LLY", "MRK", "NVO",
    "NVS", "PFE", "REGN", "ROG", "SNY",
}

COMPLEX_SOURCE_TYPES = {"image_page", "js_cards"}
STRUCTURED_SOURCE_TYPES = {"csv_doc", "xlsx_doc"}

MODEL_PRICES = {
    HAIKU_MODEL: {"input": 1.00, "output": 5.00},
    SONNET_MODEL: {"input": 3.00, "output": 15.00},
    OPUS_MODEL: {"input": 5.00, "output": 25.00},
}


@dataclass(frozen=True)
class ModelRoute:
    extraction_model: str
    qa_model: str
    escalation_model: str | None
    complexity: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "extraction_model": self.extraction_model,
            "qa_model": self.qa_model,
            "escalation_model": self.escalation_model,
            "complexity": self.complexity,
            "reason": self.reason,
        }


def route_for_company_source(company: Company, source: CompanySource) -> ModelRoute:
    ticker = (company.ticker or "").upper()
    source_type = source.source_type or "pipeline_page"
    expected = source.known_expected_count

    if source_type in COMPLEX_SOURCE_TYPES or (expected is not None and expected >= 75):
        return ModelRoute(
            extraction_model=OPUS_MODEL,
            qa_model=SONNET_MODEL,
            escalation_model=OPUS_MODEL,
            complexity="high",
            reason="complex source type or large expected pipeline",
        )

    if ticker in HIGH_VALUE_TICKERS:
        return ModelRoute(
            extraction_model=SONNET_MODEL,
            qa_model=SONNET_MODEL,
            escalation_model=OPUS_MODEL,
            complexity="high_value",
            reason="high-value ticker gets Opus appellate QA if cheaper QA fails",
        )

    if source_type in STRUCTURED_SOURCE_TYPES and expected is not None and expected <= 3:
        return ModelRoute(
            extraction_model=HAIKU_MODEL,
            qa_model=HAIKU_MODEL,
            escalation_model=SONNET_MODEL,
            complexity="small_structured",
            reason="small structured source can use Haiku first",
        )

    if expected is not None and expected <= 3:
        return ModelRoute(
            extraction_model=SONNET_MODEL,
            qa_model=HAIKU_MODEL,
            escalation_model=SONNET_MODEL,
            complexity="small",
            reason="small pipeline uses Sonnet extraction with cheap QA",
        )

    return ModelRoute(
        extraction_model=SONNET_MODEL,
        qa_model=SONNET_MODEL,
        escalation_model=OPUS_MODEL,
        complexity="normal",
        reason="normal source uses Sonnet with Opus reserved for unresolved QA",
    )


def estimate_call_cost(model: str, input_tokens: int, output_tokens: int, batch: bool = False) -> float:
    prices = MODEL_PRICES[model]
    discount = 0.5 if batch else 1.0
    return discount * (
        (input_tokens / 1_000_000) * prices["input"]
        + (output_tokens / 1_000_000) * prices["output"]
    )


def estimate_company_cost(
    route: ModelRoute,
    extraction_input_tokens: int = 60_000,
    extraction_output_tokens: int = 10_000,
    qa_input_tokens: int = 50_000,
    qa_output_tokens: int = 2_000,
    escalation_rate: float = 0.1,
    batch: bool = True,
) -> dict:
    extraction = estimate_call_cost(
        route.extraction_model, extraction_input_tokens, extraction_output_tokens, batch=batch,
    )
    qa = estimate_call_cost(route.qa_model, qa_input_tokens, qa_output_tokens, batch=batch)
    escalation = 0.0
    if route.escalation_model:
        escalation = escalation_rate * estimate_call_cost(
            route.escalation_model, qa_input_tokens, qa_output_tokens, batch=batch,
        )
    total = extraction + qa + escalation
    return {
        "route": route.as_dict(),
        "batch": batch,
        "extraction_cost_usd": round(extraction, 4),
        "qa_cost_usd": round(qa, 4),
        "expected_escalation_cost_usd": round(escalation, 4),
        "total_cost_usd": round(total, 4),
    }
