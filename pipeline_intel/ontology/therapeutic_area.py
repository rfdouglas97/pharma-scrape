"""Roll mapped indications up to a high-level therapeutic area via the MONDO is-a
hierarchy — the classification axis investors group pharma pipelines by (Oncology,
Immunology, Neuroscience, ...).

For each mapped disease we fetch its full ancestor chain and match against a priority-
ordered set of MONDO top-level anchors. Priority matters: a lung cancer is BOTH 'cancer'
and 'respiratory system disorder' — Oncology must win. Anchors are verified MONDO CURIEs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import IndicationMapping
from pipeline_intel.ontology import ols_client

# Priority-ordered: first matching anchor wins (so a lung cancer is Oncology, not Respiratory;
# lupus is Immunology, not Renal). Multiple anchor CURIEs can map to one area because MONDO
# routes diseases through different intermediate categories (e.g. melanoma is under 'neoplasm'
# but not the 'cancer' subclass). All CURIEs verified against MONDO.
TA_ANCHORS: list[tuple[str, str]] = [
    ("MONDO:0004992", "Oncology"),                   # cancer
    ("MONDO:0005070", "Oncology"),                   # neoplasm (covers melanoma, etc.)
    ("MONDO:0045024", "Oncology"),                   # cancer or benign tumor
    ("MONDO:0005550", "Infectious Disease & Vaccines"),
    ("MONDO:0005046", "Immunology & Inflammation"),  # immune system disorder
    ("MONDO:0007179", "Immunology & Inflammation"),  # autoimmune disease
    ("MONDO:0005554", "Immunology & Inflammation"),  # rheumatic disorder
    ("MONDO:0000605", "Immunology & Inflammation"),  # hypersensitivity reaction disease
    ("MONDO:0005271", "Immunology & Inflammation"),  # allergic disease
    ("MONDO:0005071", "Neuroscience"),               # nervous system disorder
    ("MONDO:0002025", "Neuroscience"),               # psychiatric disorder
    ("MONDO:0004995", "Cardiovascular"),
    ("MONDO:0005385", "Cardiovascular"),             # vascular disorder (hypertension, PAH)
    ("MONDO:0005066", "Metabolic & Endocrine"),
    ("MONDO:0005151", "Metabolic & Endocrine"),      # endocrine system disorder
    ("MONDO:0005137", "Metabolic & Endocrine"),      # nutritional disorder (obesity)
    ("MONDO:0005087", "Respiratory"),
    ("MONDO:0005570", "Hematology (non-malignant)"),
    ("MONDO:0005328", "Ophthalmology"),
    ("MONDO:0005240", "Renal"),
    ("MONDO:0004335", "Gastrointestinal"),
    ("MONDO:0005093", "Dermatology"),
    ("MONDO:0002081", "Musculoskeletal"),            # musculoskeletal system disorder
]
OTHER = "Other / Uncategorized"


@dataclass
class TAStats:
    distinct_diseases: int = 0
    classified: int = 0
    other: int = 0
    by_area: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"distinct_diseases": self.distinct_diseases, "classified": self.classified,
                "other": self.other, "by_area": dict(sorted(self.by_area.items(), key=lambda x: -x[1]))}


def _area_for(curie: str) -> str:
    """Fetch the disease's ancestors and return the highest-priority therapeutic area."""
    term = ols_client.term_by_curie(curie)
    if term is None or not term.iri:
        return OTHER
    ancestry = set(ols_client.hierarchical_ancestors(term.iri)) | {curie}
    for anchor, ta in TA_ANCHORS:
        if anchor in ancestry:
            return ta
    return OTHER


def classify_all(s: Session) -> TAStats:
    """Assign therapeutic_area to every mapped indication. Deduped per CURIE (one OLS
    ancestor fetch per distinct disease), then written to all mappings with that CURIE."""
    stats = TAStats()
    curies = list(s.execute(
        select(IndicationMapping.curie).where(
            IndicationMapping.curie.isnot(None),
            IndicationMapping.status.in_(("auto", "reviewed")),
        ).distinct()
    ).scalars())
    stats.distinct_diseases = len(curies)

    for i, curie in enumerate(curies, 1):
        ta = _area_for(curie)
        s.execute(update(IndicationMapping).where(IndicationMapping.curie == curie)
                  .values(therapeutic_area=ta))
        stats.by_area[ta] = stats.by_area.get(ta, 0) + 1
        if ta == OTHER:
            stats.other += 1
        else:
            stats.classified += 1
        if i % 20 == 0:
            s.commit()
    s.commit()
    return stats
