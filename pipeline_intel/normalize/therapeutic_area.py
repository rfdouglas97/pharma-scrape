"""Deterministic, API-free therapeutic-area classification from a company's own page-stated
disease/therapeutic-area label (and an indication-text fallback).

This complements `pipeline_intel/ontology/therapeutic_area.py` (which rolls MONDO-mapped indications
up to a TA via the ontology hierarchy, needs the OLS API). Here we map the *page-captured* "Disease
Area"/"Therapeutic Area" field — which big-pharma pipeline pages already provide and which is clean
— straight to the SAME canonical TA labels, so the two paths produce a consistent vocabulary.

Consistency note: blood **cancers** (myeloma, lymphoma, leukemia, MDS, myelofibrosis) classify as
**Oncology** — matching the MONDO classifier (they are neoplasms). "Hematology (non-malignant)" is
reserved for thalassemia/anemia/sickle-cell/ITP. Bare organ labels in a pipeline disease-area column
(e.g. "Lung", "Bladder") denote the cancer of that organ in context and map to Oncology.
"""

from __future__ import annotations

import re

# Canonical labels mirror pipeline_intel/ontology/therapeutic_area.py.
ONCOLOGY = "Oncology"
IMMUNOLOGY = "Immunology & Inflammation"
NEURO = "Neuroscience"
CARDIO = "Cardiovascular"
HEME_BENIGN = "Hematology (non-malignant)"
INFECTIOUS = "Infectious Disease & Vaccines"
METABOLIC = "Metabolic & Endocrine"
RESPIRATORY = "Respiratory"
OTHER = "Other / Uncategorized"

# Priority-ordered (first match wins). Non-malignant heme + organ-specific non-cancer terms are
# checked before the broad oncology net so they aren't swallowed by it.
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"thalassemia|thalassaemia|sickle|\banemia\b|\banaemia\b|\bitp\b|"
                r"immune thrombocytopenia", re.I), HEME_BENIGN),
    (re.compile(r"immunolog|inflammat|autoimmune|lupus|\bsle\b|psorias|colitis|crohn|\bibd\b|"
                r"ulcerative|rheumat|arthrit|dermatitis|atopic|eosinophil|asthma|vasculit|"
                r"sjogren|scleroderma|graft", re.I), IMMUNOLOGY),
    (re.compile(r"neurolog|neuroscience|alzheimer|parkinson|psychiat|schizophren|depress|migraine|"
                r"\bcns\b|cognition|\btau\b|multiple sclerosis|\bals\b|epilepsy|huntington", re.I), NEURO),
    (re.compile(r"cardiovascular|cardiac|cardiomyopath|\bhcm\b|heart failure|thrombo|\bstroke\b|"
                r"atrial fibrillation|hypertension|\bpah\b|coronary|atheroscleros", re.I), CARDIO),
    (re.compile(r"infectio|vaccine|sars|covid|\bhiv\b|viral|antiviral|influenza|bacteria|"
                r"hepatitis [bc]", re.I), INFECTIOUS),
    (re.compile(r"diabet|obesity|metabolic|endocrine|\bnash\b|steatohepatitis|dyslipid|thyroid",
                re.I), METABOLIC),
    # Broad oncology net (incl. blood cancers and organ-cancer columns).
    (re.compile(
        r"oncolog|tumou?r|cancer|carcinoma|neoplas|lymphoma|leukem|leukaem|myeloma|melanoma|"
        r"sarcoma|glioblastoma|glioma|nsclc|sclc|\bmds\b|myelodysplas|myelofibros|myeloprolifer|"
        r"\baml\b|\bcml\b|\bcll\b|\ball\b|hematologic|haematologic|solid tumou?r|metasta|"
        r"\blung\b|\bbreast\b|\bprostate\b|\bbladder\b|colorectal|\bcrc\b|gastric|esophag|oesophag|"
        r"ovarian|cervical|pancreat|\brenal cell\b|\brcc\b|hepatocellular|\bhcc\b|head (and|&) neck|"
        r"urothelial|mesotheli|\bgist\b|hodgkin|multiple myeloma|\bmm\b", re.I), ONCOLOGY),
    (re.compile(r"respiratory|\bcopd\b|pulmonary fibros|idiopathic pulmonary", re.I), RESPIRATORY),
]


def disease_area_to_ta(*texts: str | None) -> str:
    """Classify into a canonical therapeutic area from one or more text signals (e.g. the captured
    disease-area label first, then the indication text as fallback). Returns OTHER if no rule hits."""
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return OTHER
    for rx, ta in _RULES:
        if rx.search(blob):
            return ta
    return OTHER
