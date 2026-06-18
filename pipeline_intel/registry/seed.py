"""Idempotent seed loaders for controlled vocabularies and the company registry.

All loaders upsert by natural key, so re-running is safe and reflects edits to the
YAML files (the YAML is reproducible config; the DB is the source of truth at runtime).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline_intel.db import session
from pipeline_intel.gold.models import (
    Company,
    CompanySource,
    ModalityVocab,
    PhaseVocab,
    VocabMapping,
)
from pipeline_intel.source_discovery import rank_for_source_type

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def seed_phase(s: Session) -> dict:
    data = _load_yaml(CONFIG_DIR / "vocab" / "phase.yaml")
    version = str(data.get("version", "1"))
    codes, aliases = 0, 0
    for entry in data["phases"]:
        row = s.get(PhaseVocab, entry["code"])
        if row is None:
            row = PhaseVocab(code=entry["code"])
            s.add(row)
        row.label = entry["label"]
        row.sort_order = entry.get("sort_order", 0)
        row.version = version
        codes += 1
        aliases += _seed_aliases(s, "phase", entry["code"], entry.get("aliases", []))
    return {"phase_codes": codes, "phase_aliases": aliases}


def seed_modality(s: Session) -> dict:
    data = _load_yaml(CONFIG_DIR / "vocab" / "modality.yaml")
    version = str(data.get("version", "1"))
    codes, aliases = 0, 0
    for entry in data["modalities"]:
        row = s.get(ModalityVocab, entry["code"])
        if row is None:
            row = ModalityVocab(code=entry["code"])
            s.add(row)
        row.label = entry["label"]
        row.version = version
        codes += 1
        aliases += _seed_aliases(s, "modality", entry["code"], entry.get("aliases", []))
    return {"modality_codes": codes, "modality_aliases": aliases}


def _seed_aliases(s: Session, vocab: str, code: str, aliases: list[str]) -> int:
    """Pre-populate vocab_mapping with deterministic alias->code mappings (status=reviewed).

    These are the high-confidence dictionary matches the normalizer tries before any
    LLM fallback. The canonical code maps to itself too.
    """
    n = 0
    for verbatim in [code.replace("_", " "), *aliases]:
        existing = s.execute(
            select(VocabMapping).where(
                VocabMapping.vocab == vocab,
                VocabMapping.verbatim == verbatim,
            )
        ).scalar_one_or_none()
        if existing is None:
            s.add(
                VocabMapping(
                    vocab=vocab, verbatim=verbatim, code=code,
                    confidence=1.0, status="reviewed",
                )
            )
            n += 1
        else:
            existing.code = code
            existing.confidence = 1.0
            existing.status = "reviewed"
    return n


def seed_companies(s: Session) -> dict:
    data = _load_yaml(CONFIG_DIR / "companies.seed.yaml")
    companies, sources = 0, 0
    for entry in data["companies"]:
        company = s.execute(
            select(Company).where(Company.name == entry["name"])
        ).scalar_one_or_none()
        if company is None:
            company = Company(name=entry["name"])
            s.add(company)
            s.flush()  # assign company_id for FK below
        company.ticker = entry.get("ticker")
        company.exchange = entry.get("exchange")
        company.country = entry.get("country")
        company.website = entry.get("website")
        companies += 1

        for src in entry.get("sources", []):
            existing = s.execute(
                select(CompanySource).where(
                    CompanySource.company_id == company.company_id,
                    CompanySource.url == src["url"],
                )
            ).scalar_one_or_none()
            if existing is None:
                source_type = src.get("source_type", "pipeline_page")
                s.add(
                    CompanySource(
                        company_id=company.company_id,
                        url=src["url"],
                        source_type=source_type,
                        preferred_source_rank=src.get(
                            "preferred_source_rank",
                            rank_for_source_type(source_type),
                        ),
                        known_expected_count=src.get("known_expected_count"),
                        render_config=src.get("render_config", {}),
                    )
                )
                sources += 1
            else:
                source_type = src.get("source_type", "pipeline_page")
                existing.source_type = source_type
                existing.preferred_source_rank = src.get(
                    "preferred_source_rank",
                    rank_for_source_type(source_type),
                )
                if "known_expected_count" in src:
                    existing.known_expected_count = src["known_expected_count"]
                if "render_config" in src:
                    existing.render_config = src["render_config"]
    return {"companies": companies, "sources": sources}


def seed_all() -> dict:
    stats: dict = {}
    with session() as s:
        stats |= seed_phase(s)
        stats |= seed_modality(s)
        stats |= seed_companies(s)
    return stats
