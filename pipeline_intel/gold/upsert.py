"""Thin silver -> gold loader.

Turns a validated ExtractionResult into company/asset/program/program_version rows.
Scope (M2, deliberately thin):
- Asset identity = exact normalized name/synonym match (via asset_synonym). This already
  deduplicates partnered assets when companies use the same name/code. Fuzzy + LLM
  entity resolution is M4.
- Indication/target = upsert by normalized label, NO ontology mapping yet (M4).
- Phase/modality = dictionary normalization (normalize/vocab.py); unmapped -> review queue.
- program_version is SCD2: one open row per program; a change in (phase, status) closes
  the open row and opens a new one. Unchanged re-loads just touch last_seen (idempotent).
Every value keeps its verbatim form; asset/program extras preserve disclosed fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pipeline_intel.extract.schemas import ExtractedAsset, ExtractionResult
from pipeline_intel.gold.models import (
    Asset,
    AssetSynonym,
    AssetTarget,
    CompanySource,
    Extraction,
    Indication,
    Partnership,
    Program,
    ProgramVersion,
    Snapshot,
    Target,
)
from pipeline_intel.normalize.vocab import normalize_modality, normalize_phase

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DEV_CODE = re.compile(r"^[A-Za-z]{1,6}[- ]?\d{2,}")


def _norm(s: str | None) -> str:
    return _NON_ALNUM.sub("", s.lower()).strip() if s else ""


def _classify_status(status_verbatim: str | None, phase_verbatim: str | None, phase_code: str | None) -> str:
    blob = f"{status_verbatim or ''} {phase_verbatim or ''}".lower()
    discontinued_words = ("discontinu", "terminat", "removed", "withdrawn")
    if phase_code == "discontinued" or any(w in blob for w in discontinued_words):
        return "discontinued"
    if any(w in blob for w in ("hold", "pause", "suspend")):
        return "paused"
    return "active"


@dataclass
class LoadStats:
    extraction_id: str
    assets_new: int = 0
    assets_matched: int = 0
    programs_new: int = 0
    versions_new: int = 0
    versions_changed: int = 0
    versions_touched: int = 0
    unmapped_phase: int = 0
    unmapped_modality: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _resolve_asset(
    s: Session, ea: ExtractedAsset, company_id: str, extraction_id: str, stats: LoadStats
) -> Asset:
    names = [ea.preferred_name, *ea.synonyms]
    name_norms = [n for n in (_norm(x) for x in names) if n]

    asset_ids = set()
    if name_norms:
        asset_ids = set(
            s.execute(
                select(AssetSynonym.asset_id).where(func.lower(AssetSynonym.synonym).in_(
                    [n for n in (x.strip().lower() for x in names) if n]
                ))
            ).scalars().all()
        )
        # also try matching the normalized (punctuation-stripped) forms held in synonyms
        if not asset_ids:
            for row_id, syn in s.execute(select(AssetSynonym.asset_id, AssetSynonym.synonym)).all():
                if _norm(syn) in name_norms:
                    asset_ids.add(row_id)

    if asset_ids:
        asset = s.get(Asset, next(iter(asset_ids)))
        stats.assets_matched += 1
    else:
        asset = Asset(preferred_name=ea.preferred_name, extras={})
        s.add(asset)
        s.flush()
        stats.assets_new += 1

    # Fill modality if not already set
    if ea.modality_verbatim and not asset.modality_code:
        asset.modality_code = normalize_modality(s, ea.modality_verbatim)
        asset.modality_verbatim = ea.modality_verbatim
        if asset.modality_code is None:
            stats.unmapped_modality += 1

    # Mechanism / mode of action is a PRIMARY disclosed field — store it first-class.
    # Companies disclose the target/mechanism here (e.g. GSK's "Mode of Action" column),
    # so this is the disclosed target/mechanism, not a generic extra.
    if ea.mechanism_verbatim and not asset.mechanism_verbatim:
        asset.mechanism_verbatim = ea.mechanism_verbatim

    # Other asset-level extras kept verbatim (originator, page-specific fields).
    extras = dict(asset.extras or {})
    if ea.originator_verbatim:
        extras.setdefault("originator_verbatim", ea.originator_verbatim)
    for f in ea.additional_fields:
        extras.setdefault(f.name, f.value)
    asset.extras = extras

    # Maintain the synonym index (drives identity matching). preferred + all synonyms.
    existing_syn = {
        _norm(x)
        for x in s.execute(
            select(AssetSynonym.synonym).where(AssetSynonym.asset_id == asset.asset_id)
        ).scalars().all()
    }
    for nm in names:
        if _norm(nm) and _norm(nm) not in existing_syn:
            syn_type = "primary" if nm == ea.preferred_name else (
                "dev_code" if _DEV_CODE.match(nm) else "other"
            )
            s.add(AssetSynonym(
                asset_id=asset.asset_id, synonym=nm, synonym_type=syn_type,
                source_extraction_id=extraction_id,
            ))
            existing_syn.add(_norm(nm))

    # Target (thin: store verbatim; HGNC/UniProt normalization is M4)
    if ea.target_verbatim:
        _link_target(s, asset.asset_id, ea.target_verbatim, extraction_id)

    # Partners
    for p in ea.partners:
        _link_partner(s, asset.asset_id, p.name, p.role, p.territory, extraction_id)

    return asset


def _link_target(s: Session, asset_id: str, verbatim: str, extraction_id: str) -> None:
    target = s.execute(
        select(Target).where(func.lower(Target.name) == verbatim.strip().lower())
    ).scalar_one_or_none()
    if target is None:
        target = Target(name=verbatim)
        s.add(target)
        s.flush()
    exists = s.execute(
        select(AssetTarget).where(AssetTarget.asset_id == asset_id, AssetTarget.target_id == target.target_id)
    ).scalar_one_or_none()
    if exists is None:
        s.add(AssetTarget(asset_id=asset_id, target_id=target.target_id, verbatim=verbatim,
                          source_extraction_id=extraction_id))


def _clamp(value: str | None, maxlen: int) -> str | None:
    """Truncate a model-provided string to its DB column length. The model sometimes drops a
    verbose value into a short column (e.g. a full deal description into `role` (varchar 64)),
    which would otherwise crash the whole gold load with StringDataRightTruncation."""
    if value is None:
        return None
    return value if len(value) <= maxlen else value[: maxlen - 1] + "…"


def _link_partner(s: Session, asset_id: str, name: str, role, territory, extraction_id: str) -> None:
    exists = s.execute(
        select(Partnership).where(
            Partnership.asset_id == asset_id,
            func.lower(Partnership.partner_name_verbatim) == name.strip().lower(),
        )
    ).scalar_one_or_none()
    if exists is None:
        s.add(Partnership(asset_id=asset_id, partner_name_verbatim=name, role=_clamp(role, 64),
                          territory=territory, source_extraction_id=extraction_id))


def _resolve_indication(s: Session, label: str) -> Indication:
    ind = s.execute(
        select(Indication).where(func.lower(Indication.preferred_label) == label.strip().lower())
    ).scalar_one_or_none()
    if ind is None:
        ind = Indication(preferred_label=label)
        s.add(ind)
        s.flush()
    return ind


def _apply_scd2(
    s: Session, program_id: str, phase_code, phase_verbatim, status, indication_verbatim,
    extras: dict, snapshot_id: str, stats: LoadStats,
) -> None:
    open_v = s.execute(
        select(ProgramVersion).where(
            ProgramVersion.program_id == program_id, ProgramVersion.valid_to.is_(None)
        )
    ).scalar_one_or_none()

    if open_v is None:
        s.add(ProgramVersion(
            program_id=program_id, phase_code=phase_code, phase_verbatim=phase_verbatim,
            status=status, indication_verbatim=indication_verbatim, extras=extras,
            first_seen_snapshot_id=snapshot_id, last_seen_snapshot_id=snapshot_id,
        ))
        stats.versions_new += 1
        return

    if (open_v.phase_code, open_v.status) == (phase_code, status):
        open_v.last_seen_snapshot_id = snapshot_id  # unchanged: just extend coverage
        open_v.phase_verbatim = phase_verbatim
        open_v.indication_verbatim = indication_verbatim
        open_v.extras = extras
        stats.versions_touched += 1
        return

    open_v.valid_to = datetime.now(UTC)  # state changed: close + open new (history)
    open_v.last_seen_snapshot_id = snapshot_id
    s.add(ProgramVersion(
        program_id=program_id, phase_code=phase_code, phase_verbatim=phase_verbatim,
        status=status, indication_verbatim=indication_verbatim, extras=extras,
        first_seen_snapshot_id=snapshot_id, last_seen_snapshot_id=snapshot_id,
    ))
    stats.versions_changed += 1


def load_extraction(s: Session, extraction_id: str) -> LoadStats:
    stats = LoadStats(extraction_id=extraction_id)
    ext = s.get(Extraction, extraction_id)
    if ext is None or not ext.raw_json:
        stats.notes.append("no extraction / empty raw_json")
        return stats

    snap = s.get(Snapshot, ext.snapshot_id)
    source = s.get(CompanySource, snap.source_id) if snap else None
    if source is None:
        stats.notes.append("snapshot/source missing")
        return stats
    company_id = source.company_id

    result = ExtractionResult.model_validate(ext.raw_json)
    for ea in result.assets:
        asset = _resolve_asset(s, ea, company_id, extraction_id, stats)
        for ep in ea.programs:
            ind = _resolve_indication(s, ep.indication_verbatim)
            program = s.execute(
                select(Program).where(
                    Program.asset_id == asset.asset_id,
                    Program.indication_id == ind.indication_id,
                    Program.company_id == company_id,
                )
            ).scalar_one_or_none()
            if program is None:
                program = Program(asset_id=asset.asset_id, indication_id=ind.indication_id,
                                  company_id=company_id)
                s.add(program)
                s.flush()
                stats.programs_new += 1

            phase_code = normalize_phase(s, ep.phase_verbatim)
            if phase_code is None and ep.phase_verbatim:
                stats.unmapped_phase += 1
            status = _classify_status(ep.status, ep.phase_verbatim, phase_code)
            prog_extras = {f.name: f.value for f in ep.additional_fields}
            if ep.status:
                prog_extras.setdefault("status_verbatim", ep.status)
            _apply_scd2(s, program.program_id, phase_code, ep.phase_verbatim, status,
                        ep.indication_verbatim, prog_extras, ext.snapshot_id, stats)

    return stats
