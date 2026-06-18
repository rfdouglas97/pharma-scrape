"""SQLAlchemy ORM models — the full medallion schema (bronze/silver/gold).

ID convention: app-generated ULIDs (string, 26 chars). Every gold row traces
back to a snapshot_id + extraction_id for provenance.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from ulid import ULID


def new_id() -> str:
    return str(ULID())


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[str]:
    return mapped_column(String(26), primary_key=True, default=new_id)


def ts_now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------
# Identity & provenance
# --------------------------------------------------------------------------
class Company(Base):
    __tablename__ = "company"
    company_id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(32))
    exchange: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(64))
    website: Mapped[str | None] = mapped_column(Text)
    parent_company_id: Mapped[str | None] = mapped_column(ForeignKey("company.company_id"))
    status: Mapped[str] = mapped_column(String(32), default="active")
    pipeline_status: Mapped[str] = mapped_column(String(32), default="unverified_source")
    market_cap_usd: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = ts_now()

    sources: Mapped[list[CompanySource]] = relationship(back_populates="company")


class CompanySource(Base):
    """A pipeline page or PDF doc for a company — the versioned, managed registry."""

    __tablename__ = "company_source"
    source_id: Mapped[str] = pk()
    company_id: Mapped[str] = mapped_column(ForeignKey("company.company_id"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="pipeline_page")
    render_config: Mapped[dict] = mapped_column(JSONB, default=dict)  # waits, click-to-expand, pagination
    preferred_source_rank: Mapped[int] = mapped_column(Integer, default=50)
    known_expected_count: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = ts_now()

    company: Mapped[Company] = relationship(back_populates="sources")
    __table_args__ = (UniqueConstraint("company_id", "url", name="uq_company_source_url"),)


class Snapshot(Base):
    """Bronze: immutable record of one fetch. Artifacts live in object storage."""

    __tablename__ = "snapshot"
    snapshot_id: Mapped[str] = pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("company_source.source_id"), nullable=False)
    fetched_at: Mapped[datetime] = ts_now()
    # captured_at = real-world date the content was true (Wayback capture date; = fetched_at for
    # live scrapes). The temporal anchor for ordering + the UI "as-of" date. origin distinguishes
    # live weekly scrapes from historical Wayback backfill.
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(16), default="live")  # live | wayback
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 of normalized DOM
    html_key: Mapped[str | None] = mapped_column(Text)
    screenshot_keys: Mapped[list] = mapped_column(JSONB, default=list)
    pdf_keys: Mapped[list] = mapped_column(JSONB, default=list)
    render_meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    # cheap per-extraction completeness proxy, populated at extraction time; the consuming
    # distribution/redesign-detection logic comes later (column added now to avoid re-extraction).
    extraction_quality_score: Mapped[float | None] = mapped_column(Numeric)
    unchanged: Mapped[bool] = mapped_column(Boolean, default=False)  # hash matched previous

    __table_args__ = (
        Index("ix_snapshot_source_fetched", "source_id", "fetched_at"),
        Index("ix_snapshot_source_captured", "source_id", "captured_at"),
    )


class Extraction(Base):
    """Silver: validated LLM extraction for one snapshot. Re-derivable from bronze."""

    __tablename__ = "extraction"
    extraction_id: Mapped[str] = pk()
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshot.snapshot_id"), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    extracted_at: Mapped[datetime] = ts_now()
    raw_json: Mapped[dict | None] = mapped_column(JSONB)  # full LLM output, verbatim
    status: Mapped[str] = mapped_column(String(32), default="ok")  # ok | failed | needs_review
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    qa_status: Mapped[str | None] = mapped_column(String(32))
    qa_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    qa_report: Mapped[dict] = mapped_column(JSONB, default=dict)
    expected_count: Mapped[int | None] = mapped_column(Integer)
    observed_count: Mapped[int | None] = mapped_column(Integer)
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)


class QAReport(Base):
    """Autonomous extraction quality verdict from deterministic checks plus an LLM judge."""

    __tablename__ = "qa_report"
    qa_report_id: Mapped[str] = pk()
    extraction_id: Mapped[str] = mapped_column(ForeignKey("extraction.extraction_id"), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(16))  # pass | warn | fail
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    expected_count: Mapped[int | None] = mapped_column(Integer)
    observed_count: Mapped[int | None] = mapped_column(Integer)
    missing_assets: Mapped[list] = mapped_column(JSONB, default=list)
    extra_assets: Mapped[list] = mapped_column(JSONB, default=list)
    suspicious_fields: Mapped[list] = mapped_column(JSONB, default=list)
    count_mismatches: Mapped[list] = mapped_column(JSONB, default=list)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts_now()

    __table_args__ = (
        Index("ix_qa_report_extraction_created", "extraction_id", "created_at"),
    )


class ModelBatch(Base):
    """Durable Anthropic Message Batch submission metadata."""

    __tablename__ = "model_batch"
    model_batch_id: Mapped[str] = pk()
    provider_batch_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # extraction | qa
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[dict] = mapped_column(JSONB, default=dict)
    provider_response: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts_now()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_model_batch_kind_status", "kind", "status"),
    )


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------
class Asset(Base):
    __tablename__ = "asset"
    asset_id: Mapped[str] = pk()
    preferred_name: Mapped[str] = mapped_column(Text, nullable=False)
    modality_code: Mapped[str | None] = mapped_column(ForeignKey("modality_vocab.code"))
    modality_verbatim: Mapped[str | None] = mapped_column(Text)
    # Provenance for modality: 'disclosed' (from the company page) vs 'open_targets' (backfilled).
    modality_source: Mapped[str] = mapped_column(String(16), default="disclosed")
    originator_company_id: Mapped[str | None] = mapped_column(ForeignKey("company.company_id"))
    chembl_id: Mapped[str | None] = mapped_column(String(32))
    # Mechanism / mode of action as DISCLOSED by the company (a primary pharma field).
    # This is the company's target/mechanism statement, e.g. "Ileal bile acid transporter
    # inhibitor", "anti-IL5 antibody". First-class, never overwritten by enrichment.
    mechanism_verbatim: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # Asset-level company-specific fields preserved verbatim: mechanism_verbatim,
    # originator_verbatim, and any asset additional_fields the page disclosed.
    extras: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts_now()

    synonyms: Mapped[list[AssetSynonym]] = relationship(back_populates="asset")


class AssetSynonym(Base):
    __tablename__ = "asset_synonym"
    id: Mapped[str] = pk()
    asset_id: Mapped[str] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    synonym: Mapped[str] = mapped_column(Text, nullable=False)
    synonym_type: Mapped[str] = mapped_column(String(32), default="other")  # dev_code|brand|generic|other
    source_extraction_id: Mapped[str | None] = mapped_column(ForeignKey("extraction.extraction_id"))

    asset: Mapped[Asset] = relationship(back_populates="synonyms")
    __table_args__ = (
        Index("ix_asset_synonym_lower", asset_id, func.lower(synonym), unique=True),
        Index("ix_asset_synonym_trgm", "synonym"),
    )


class Indication(Base):
    __tablename__ = "indication"
    indication_id: Mapped[str] = pk()
    preferred_label: Mapped[str] = mapped_column(Text, nullable=False)


class IndicationMapping(Base):
    __tablename__ = "indication_mapping"
    id: Mapped[str] = pk()
    indication_id: Mapped[str] = mapped_column(ForeignKey("indication.indication_id"), nullable=False)
    ontology: Mapped[str] = mapped_column(String(16))  # EFO | MONDO
    curie: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    method: Mapped[str | None] = mapped_column(String(32))  # exact | ols_search | llm_assisted
    status: Mapped[str] = mapped_column(String(32), default="auto")  # auto|reviewed|rejected|unmapped
    # High-level therapeutic area (Oncology, Immunology, Neuroscience, ...) derived from the
    # MONDO is-a hierarchy — the classification axis investors group pipelines by.
    therapeutic_area: Mapped[str | None] = mapped_column(String(48))


class Target(Base):
    __tablename__ = "target"
    target_id: Mapped[str] = pk()
    hgnc_symbol: Mapped[str | None] = mapped_column(String(64))
    uniprot_id: Mapped[str | None] = mapped_column(String(32))
    ensembl_id: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(Text)


class AssetTarget(Base):
    __tablename__ = "asset_target"
    id: Mapped[str] = pk()
    asset_id: Mapped[str] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    target_id: Mapped[str] = mapped_column(ForeignKey("target.target_id"), nullable=False)
    verbatim: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(64))  # inhibitor / agonist / ...
    # 'disclosed' (company page) vs 'open_targets' (mechanism-of-action backfill).
    source: Mapped[str] = mapped_column(String(16), default="disclosed")
    source_extraction_id: Mapped[str | None] = mapped_column(ForeignKey("extraction.extraction_id"))
    __table_args__ = (UniqueConstraint("asset_id", "target_id", name="uq_asset_target"),)


class Program(Base):
    """The atomic unit: asset x indication x sponsoring company."""

    __tablename__ = "program"
    program_id: Mapped[str] = pk()
    asset_id: Mapped[str] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    indication_id: Mapped[str] = mapped_column(ForeignKey("indication.indication_id"), nullable=False)
    company_id: Mapped[str] = mapped_column(ForeignKey("company.company_id"), nullable=False)
    __table_args__ = (
        UniqueConstraint("asset_id", "indication_id", "company_id", name="uq_program"),
    )


class ProgramVersion(Base):
    """SCD2 state history. Exactly one open row (valid_to IS NULL) per program."""

    __tablename__ = "program_version"
    version_id: Mapped[str] = pk()
    program_id: Mapped[str] = mapped_column(ForeignKey("program.program_id"), nullable=False)
    phase_code: Mapped[str | None] = mapped_column(ForeignKey("phase_vocab.code"))
    phase_verbatim: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active|discontinued|paused|unknown
    indication_verbatim: Mapped[str | None] = mapped_column(Text)
    extras: Mapped[dict] = mapped_column(JSONB, default=dict)  # ALL company-specific fields — never dropped
    valid_from: Mapped[datetime] = ts_now()
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = current
    first_seen_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.snapshot_id"))
    last_seen_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.snapshot_id"))

    __table_args__ = (
        Index("uq_program_version_current", "program_id", unique=True, postgresql_where="valid_to IS NULL"),
    )


class Partnership(Base):
    __tablename__ = "partnership"
    partnership_id: Mapped[str] = pk()
    program_id: Mapped[str | None] = mapped_column(ForeignKey("program.program_id"))
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("asset.asset_id"))
    partner_company_id: Mapped[str | None] = mapped_column(ForeignKey("company.company_id"))
    partner_name_verbatim: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(String(64))
    deal_type: Mapped[str | None] = mapped_column(String(64))
    territory: Mapped[str | None] = mapped_column(Text)
    source_extraction_id: Mapped[str | None] = mapped_column(ForeignKey("extraction.extraction_id"))


# --------------------------------------------------------------------------
# Vocabularies, ontology, search
# --------------------------------------------------------------------------
class PhaseVocab(Base):
    __tablename__ = "phase_vocab"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(32), default="1")


class ModalityVocab(Base):
    __tablename__ = "modality_vocab"
    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1")


class VocabMapping(Base):
    """Verbatim -> normalized code, with unmapped state instead of silent guessing."""

    __tablename__ = "vocab_mapping"
    id: Mapped[str] = pk()
    vocab: Mapped[str] = mapped_column(String(32), nullable=False)  # phase | modality
    verbatim: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str | None] = mapped_column(String(48))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(32), default="auto")  # auto|reviewed|unmapped|rejected
    __table_args__ = (
        Index("ix_vocab_mapping_lookup", vocab, func.lower(verbatim), unique=True),
    )


class OntologyTerm(Base):
    __tablename__ = "ontology_term"
    curie: Mapped[str] = mapped_column(String(64), primary_key=True)
    ontology: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(Text)
    synonyms: Mapped[list] = mapped_column(JSONB, default=list)
    obsolete: Mapped[bool] = mapped_column(Boolean, default=False)


class OntologyEdge(Base):
    __tablename__ = "ontology_edge"
    parent_curie: Mapped[str] = mapped_column(String(64), primary_key=True)
    child_curie: Mapped[str] = mapped_column(String(64), primary_key=True)


class OntologyClosure(Base):
    """Precomputed transitive closure for fast adjacency expansion."""

    __tablename__ = "ontology_closure"
    ancestor_curie: Mapped[str] = mapped_column(String(64), primary_key=True)
    descendant_curie: Mapped[str] = mapped_column(String(64), primary_key=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)


class ProgramEmbedding(Base):
    __tablename__ = "program_embedding"
    program_id: Mapped[str] = mapped_column(ForeignKey("program.program_id"), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))
    text_hash: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))


class ReviewQueue(Base):
    __tablename__ = "review_queue"
    item_id: Mapped[str] = pk()
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    # vocab_unmapped | ontology_lowconf | extraction_anomaly | dedupe_candidate | program_missing
    entity_ref: Mapped[dict] = mapped_column(JSONB, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open | resolved | dismissed
    created_at: Mapped[datetime] = ts_now()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[dict] = mapped_column(JSONB, default=dict)


class JobRun(Base):
    __tablename__ = "job_run"
    run_id: Mapped[str] = pk()
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("company.company_id"))
    started_at: Mapped[datetime] = ts_now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|ok|failed|partial
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------
# Longitudinal change feed + identity decision store
# --------------------------------------------------------------------------
class ChangeEvent(Base):
    """Derived, rebuildable feed of pipeline changes between captures (the investor product).

    Recomputed from silver by the chronological replay; not hand-edited. A drug LEAVING the page
    is three-way ambiguous (approved-graduated / discontinued / renamed) — `exit_class` records the
    best phase-based proxy; `eff_min`/`eff_max` bound the true change date (the page only quantizes
    to capture dates). Only `confirmed` events should publish.
    """

    __tablename__ = "change_event"
    event_id: Mapped[str] = pk()
    company_id: Mapped[str] = mapped_column(ForeignKey("company.company_id"), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("asset.asset_id"))
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # asset_added | asset_left_pipeline | asset_phase_changed | partner_added | partner_removed
    # | asset_reappeared
    period: Mapped[str | None] = mapped_column(String(16))  # capture label observed in
    from_phase: Mapped[str | None] = mapped_column(String(32))
    to_phase: Mapped[str | None] = mapped_column(String(32))
    direction: Mapped[str | None] = mapped_column(String(16))   # advance | regress | lateral
    last_phase: Mapped[str | None] = mapped_column(String(32))
    exit_class: Mapped[str | None] = mapped_column(String(32))
    partner: Mapped[str | None] = mapped_column(Text)
    eff_min: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # interval start
    eff_max: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # interval end
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # confirmed | provisional
    from_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.snapshot_id"))
    to_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.snapshot_id"))
    created_at: Mapped[datetime] = ts_now()

    __table_args__ = (Index("ix_change_event_company", "company_id", "event_type"),)


class AssetAlias(Base):
    """Durable identity decision store: 'this name/cluster IS this asset'. Survives history rebuilds
    (which delete only change_event), so accumulated identity judgment — deterministic, LLM-clustered
    (merge_assets), or human-reviewed — is never recomputed or lost. method records provenance."""

    __tablename__ = "asset_alias"
    alias_id: Mapped[str] = pk()
    asset_id: Mapped[str] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(24), default="deterministic")  # deterministic|llm|human
    confidence: Mapped[float | None] = mapped_column(Numeric)
    created_at: Mapped[datetime] = ts_now()

    __table_args__ = (UniqueConstraint("alias", name="uq_asset_alias"),)


__all__ = [
    "Base", "new_id",
    "Company", "CompanySource", "Snapshot", "Extraction",
    "Asset", "AssetSynonym", "Indication", "IndicationMapping",
    "Target", "AssetTarget", "Program", "ProgramVersion", "Partnership",
    "PhaseVocab", "ModalityVocab", "VocabMapping",
    "OntologyTerm", "OntologyEdge", "OntologyClosure",
    "ProgramEmbedding", "ReviewQueue", "JobRun",
    "ChangeEvent", "AssetAlias",
]
