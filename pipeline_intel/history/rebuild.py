"""DB adapter: rebuild a company's change-event feed from silver via the detection core.

Reads the company's snapshots + extractions in captured_at order, resolves each extracted asset to
a stable gold asset_id (asset_alias decision store first, then asset_synonym) and normalizes phase,
feeds the chronological captures to `detect_changes`, and writes `change_event` rows. Idempotent:
deletes the company's existing change_events and rewrites them, so a rebuild is reproducible and
the decision store (asset_alias/asset_synonym) — which is NOT deleted — preserves identity judgment.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pipeline_intel.extract.schemas import ExtractionResult
from pipeline_intel.gold.models import (
    AssetAlias,
    AssetSynonym,
    CompanySource,
    Extraction,
    Snapshot,
)
from pipeline_intel.gold.models import (
    ChangeEvent as ChangeEventRow,
)
from pipeline_intel.history.detect import PHASE_ORDER, AssetObs, Capture, detect_changes
from pipeline_intel.normalize.partner import normalize_partner
from pipeline_intel.normalize.vocab import normalize_phase


def _match_asset_id(s: Session, name: str, synonyms: list[str]) -> str | None:
    """Resolve to an EXISTING gold asset_id. Alias decision store wins over the synonym index."""
    lowered = [n.strip().lower() for n in [name, *synonyms] if n and n.strip()]
    if not lowered:
        return None
    aid = s.execute(
        select(AssetAlias.asset_id).where(func.lower(AssetAlias.alias).in_(lowered))
    ).scalars().first()
    if aid:
        return aid
    return s.execute(
        select(AssetSynonym.asset_id).where(func.lower(AssetSynonym.synonym).in_(lowered))
    ).scalars().first()


def _captures_for_company(
    s: Session, company_id: str, origins: tuple[str, ...] | None = None
) -> tuple[list[Capture], dict[str, str]]:
    conds = [
        CompanySource.company_id == company_id,
        Extraction.status != "failed",
        Extraction.raw_json.isnot(None),
    ]
    if origins:
        conds.append(Snapshot.origin.in_(origins))
    rows = s.execute(
        select(Snapshot, Extraction)
        .join(Extraction, Extraction.snapshot_id == Snapshot.snapshot_id)
        .join(CompanySource, CompanySource.source_id == Snapshot.source_id)
        .where(*conds)
        .order_by(func.coalesce(Snapshot.captured_at, Snapshot.fetched_at))
    ).all()

    captures: list[Capture] = []
    snap_by_period: dict[str, str] = {}
    for snap, ext in rows:
        result = ExtractionResult.model_validate(ext.raw_json)
        by: dict[str, dict] = {}
        for ea in result.assets:
            aid = _match_asset_id(s, ea.preferred_name, ea.synonyms)
            if aid is None:
                continue  # asset not yet in gold — load_extraction creates assets; skip if absent
            rec = by.setdefault(aid, {"name": ea.preferred_name, "phase_code": None, "partners": set()})
            rec["partners"] |= {pn for p in ea.partners if (pn := normalize_partner(p.name))}
            for ep in ea.programs:
                code = normalize_phase(s, ep.phase_verbatim)
                if code and PHASE_ORDER.get(code, 0) > PHASE_ORDER.get(rec["phase_code"], -1):
                    rec["phase_code"] = code
        cap_date = (snap.captured_at or snap.fetched_at).date()
        # prefer the human quarter label ("2021Q1") so the feed and the distribution charts share an
        # x-axis; fall back to the ISO capture date for live snapshots with no quarter in render_meta.
        period = (snap.render_meta or {}).get("quarter") or cap_date.isoformat()
        snap_by_period[period] = snap.snapshot_id
        assets = tuple(
            AssetObs(asset_id=aid, name=r["name"], phase_code=r["phase_code"],
                     partners=frozenset(r["partners"]))
            for aid, r in by.items()
        )
        captures.append(Capture(period=period, captured_at=cap_date, assets=assets))
    return captures, snap_by_period


def rebuild_history(
    s: Session, company_id: str, *, confirm_n: int = 2, origins: tuple[str, ...] | None = None
) -> dict:
    """Recompute the company's change-event feed from silver. Returns summary stats.

    `origins` restricts the captures used (e.g. ("wayback",) for the backfilled historical feed,
    keeping a stray live scrape from polluting the quarterly timeline)."""
    captures, snap_by_period = _captures_for_company(s, company_id, origins)
    events, quarantined = detect_changes(captures, confirm_n=confirm_n)

    s.execute(delete(ChangeEventRow).where(ChangeEventRow.company_id == company_id))
    for e in events:
        s.add(ChangeEventRow(
            company_id=company_id, asset_id=e.asset_id, event_type=e.type, period=e.period,
            from_phase=e.from_phase, to_phase=e.to_phase, direction=e.direction,
            last_phase=e.last_phase, exit_class=e.exit_class, partner=e.partner,
            eff_min=e.eff_min, eff_max=e.eff_max, status=e.status,
            to_snapshot_id=snap_by_period.get(e.period),
        ))
    s.flush()
    return {
        "company_id": company_id,
        "captures": len(captures),
        "events": len(events),
        "quarantined": [q["period"] for q in quarantined],
        "by_type": dict(Counter(e.type for e in events)),
    }
