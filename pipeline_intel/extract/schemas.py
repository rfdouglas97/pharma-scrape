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


# --- Visual evidence (image-backed pipeline charts) -------------------------------------
# Image-only pipelines (phase-bar charts) encode the phase in BAR GEOMETRY, not text. The
# two-pass visual extractor first transcribes the chart row-by-row into VisualEvidenceRows
# (with per-row confidence + how the phase was read), then normalizes to ExtractionResult.
# Keeping the intermediate transcription makes the phase reasoning auditable and QA-able.

VISUAL_SCHEMA_VERSION = "1"


class VisualEvidenceRow(BaseModel):
    """One row read off a pipeline chart image, with provenance for how it was read."""

    asset_name: str = Field(description="Program/asset label exactly as shown (e.g. 'KB407')")
    group: str | None = Field(
        default=None, description="Therapeutic-area / section grouping label if the chart groups rows"
    )
    indication: str | None = Field(default=None, description="Indication text in the row, verbatim")
    target: str | None = Field(
        default=None,
        description="Target/payload column value if present (e.g. a 'Payload' or 'Target' column)",
    )
    modality: str | None = Field(
        default=None, description="Modality if explicitly shown for the row; null if not shown"
    )
    phase: str = Field(
        description="Phase the row's progress bar REACHES, named from the chart's phase-axis "
        "column headers (e.g. 'Phase 1/2', 'Registrational', 'Commercial'). 'Unknown' if unreadable."
    )
    status: str | None = Field(
        default=None,
        description="Status distinct from phase if shown (e.g. 'Approved', 'Commercial', 'Discontinued')",
    )
    phase_evidence: str = Field(
        description="How the phase was determined, e.g. \"bar ends at the right edge of the "
        "'Phase 1/2' column\". This is the audit trail for the visual phase call."
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence this row was read correctly (0-1)"
    )


class VisualTranscription(BaseModel):
    """Pass-1 output: the chart reconstructed row-by-row before schema normalization."""

    phase_columns: list[str] = Field(
        description="The chart's phase-axis column headers, left to right, exactly as labeled"
    )
    rows: list[VisualEvidenceRow] = Field(description="One entry per row read off the chart")
    chart_notes: str | None = Field(
        default=None,
        description="Caveats: ambiguous bars, unreadable cells, legend/footnote definitions used.",
    )
