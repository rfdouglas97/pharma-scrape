"""Pure-function tests for the indication mapper (no network/LLM)."""

import pytest

from pipeline_intel.ontology.mapper import clean_label


@pytest.mark.parametrize("raw,expected_contains", [
    ("2L+ Multiple myeloma combination with Pomalyst and dexamethasone", "Multiple myeloma"),
    ("Eosinophilic granulomatosis with polyangiitis (EGPA)", "Eosinophilic granulomatosis with polyangiitis"),
    ("1L endometrial cancer", "endometrial cancer"),
    ("Refractory chronic cough (RCC)", "Refractory chronic cough"),
    ("Respiratory syncytial virus prophylaxis, adults 60+ years of age in China",
     "Respiratory syncytial virus prophylaxis"),
])
def test_clean_label_strips_noise(raw, expected_contains):
    cleaned = clean_label(raw)
    assert expected_contains.lower() in cleaned.lower()
    # parentheticals and combo tails are gone
    assert "(" not in cleaned
    assert "combination with" not in cleaned.lower()


def test_clean_label_drops_line_of_therapy_prefix():
    assert not clean_label("3L gastric cancer").lower().startswith("3l")
    assert "gastric cancer" in clean_label("3L gastric cancer").lower()
