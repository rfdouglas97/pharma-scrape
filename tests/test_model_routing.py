from pipeline_intel.extract.client import HAIKU_MODEL, OPUS_MODEL, SONNET_MODEL
from pipeline_intel.gold.models import Company, CompanySource
from pipeline_intel.model_routing import estimate_company_cost, route_for_company_source


def _company(ticker: str = "SMOL") -> Company:
    return Company(name="Small Bio", ticker=ticker)


def _source(source_type: str = "pipeline_page", known_expected_count: int | None = None) -> CompanySource:
    return CompanySource(
        company_id="company",
        url="https://example.com/pipeline",
        source_type=source_type,
        known_expected_count=known_expected_count,
    )


def test_small_structured_source_uses_haiku_first():
    route = route_for_company_source(_company(), _source("xlsx_doc", 2))
    assert route.extraction_model == HAIKU_MODEL
    assert route.qa_model == HAIKU_MODEL
    assert route.escalation_model == SONNET_MODEL


def test_high_value_company_gets_opus_escalation_not_default_extraction():
    route = route_for_company_source(_company("PFE"), _source("pipeline_page", 20))
    assert route.extraction_model == SONNET_MODEL
    assert route.qa_model == SONNET_MODEL
    assert route.escalation_model == OPUS_MODEL


def test_complex_source_uses_opus_extraction():
    route = route_for_company_source(_company(), _source("image_page", 20))
    assert route.extraction_model == OPUS_MODEL
    assert route.escalation_model == OPUS_MODEL


def test_cost_estimate_includes_batch_discount():
    route = route_for_company_source(_company("PFE"), _source("pipeline_page", 20))
    batched = estimate_company_cost(route, batch=True)
    unbatched = estimate_company_cost(route, batch=False)
    assert batched["total_cost_usd"] < unbatched["total_cost_usd"]
