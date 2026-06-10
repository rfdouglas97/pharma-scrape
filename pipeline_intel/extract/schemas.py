"""Canonical extraction schema — the contract between the LLM and normalization.

Design rules (from the brief):
- Mirrors the gold model: an asset carries asset-level attributes and a list of
  programs (asset x indication); PHASE lives on the program, never on the asset.
- Every captured value is the company's VERBATIM text. Normalization to controlled
  vocabularies and ontology IDs happens downstream (M2), never here — the extractor's
  job is faithful capture, not interpretation.
- `additional_fields` is a list of name/value pairs, not an open dict, so the schema is
  compatible with strict structured outputs (arbitrary-key objects aren't expressible).
  Anything a company discloses that has no dedicated field lands here and is preserved.

Optional scalars are nullable; lists default to empty. This shape round-trips through
`client.messages.parse()` (Pydantic) and `output_config.format` (strict JSON schema).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

EXTRACTION_SCHEMA_VERSION = "1"


class ExtraField(BaseModel):
    """A company-specific field with no dedicated column — preserved verbatim."""

    name: str = Field(description="The field label exactly as shown, e.g. 'Route of administration'")
    value: str = Field(description="The field value exactly as shown")


class Partner(BaseModel):
    name: str = Field(description="Collaborator / partner company name as shown")
    role: str | None = Field(
        default=None,
        description="Partner role if stated, e.g. 'licensor', 'co-development', 'commercialization'",
    )
    territory: str | None = Field(
        default=None, description="Geography/territory of the partnership if stated"
    )


class ExtractedProgram(BaseModel):
    """One asset-in-indication: the atomic unit that carries a phase and status."""

    indication_verbatim: str = Field(description="Indication / disease exactly as shown")
    phase_verbatim: str = Field(
        description="Development phase exactly as shown (e.g. 'Phase 2', 'Ph1/2', 'Filed')"
    )
    status: str | None = Field(
        default=None,
        description="Status if distinct from phase, e.g. 'discontinued', 'on hold'. Null if not shown.",
    )
    additional_fields: list[ExtraField] = Field(
        default_factory=list,
        description="Any program-level fields with no dedicated slot (milestones, designations, etc.)",
    )


class ExtractedAsset(BaseModel):
    preferred_name: str = Field(description="Primary asset name/identifier as shown")
    synonyms: list[str] = Field(
        default_factory=list,
        description="Other names, development codes, brand/generic names shown for this asset",
    )
    modality_verbatim: str | None = Field(
        default=None,
        description="Drug type/modality as shown (e.g. 'mAb', 'small molecule'); null if absent.",
    )
    target_verbatim: str | None = Field(
        default=None, description="Molecular target as shown (e.g. 'PD-1', 'KRAS G12C'). Null if absent."
    )
    mechanism_verbatim: str | None = Field(
        default=None, description="Mechanism of action as shown, if distinct from the target. Null if absent."
    )
    originator_verbatim: str | None = Field(
        default=None, description="Originator / lead / source of the asset if stated. Null if absent."
    )
    partners: list[Partner] = Field(
        default_factory=list, description="Collaborators/partners shown for this asset"
    )
    programs: list[ExtractedProgram] = Field(
        description="One entry per indication this asset is being developed in (each with its own phase)"
    )
    additional_fields: list[ExtraField] = Field(
        default_factory=list,
        description="Any asset-level fields with no dedicated slot (route of administration, etc.)",
    )


class ExtractionResult(BaseModel):
    """Top-level extractor output for one rendered pipeline page."""

    assets: list[ExtractedAsset] = Field(
        description="Every distinct asset/molecule disclosed on the page"
    )
    page_notes: str | None = Field(
        default=None,
        description=(
            "Extraction caveats relevant to data quality: e.g. 'pipeline shown only as an image', "
            "'data continues in a linked PDF', 'table truncated'. Null if none."
        ),
    )
