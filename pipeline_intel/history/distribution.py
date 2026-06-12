"""Per-quarter pipeline composition over time, for the history view.

Two lenses, each labelled with its unit:
- **Distributions** (phase mix, therapeutic-area mix) are counted by **program** (asset×indication) —
  the conventional "pipeline distribution" unit and what surfaces the real shift in development
  activity (e.g. BMS's oncology program share declining as it diversifies). TA comes from the page's
  own captured disease-area field, classified deterministically (no API).
- **Pipeline size / additions / exits** (the flow + change feed) are by **compound** (asset), since a
  discontinuation/approval is a compound-level event.

Quarantined quarters (partial-render captures) are flagged via the same bad-capture guard the change
feed uses — carried in the series but marked, never silently dropped or plotted as a real value.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pipeline_intel.extract.schemas import ExtractionResult
from pipeline_intel.gold.models import Asset, ChangeEvent, CompanySource, Extraction, Snapshot
from pipeline_intel.history.rebuild import _captures_for_company
from pipeline_intel.normalize.therapeutic_area import disease_area_to_ta
from pipeline_intel.normalize.vocab import normalize_phase

PHASE_LABELS = {
    "preclinical": "Preclinical", "phase_1": "Phase 1", "phase_1_2": "Phase 1/2", "phase_2": "Phase 2",
    "phase_2_3": "Phase 2/3", "phase_3": "Phase 3", "filed": "Filed", "approved": "Approved",
    "discontinued": "Discontinued",
}
DROP = 0.6  # quarantine if a capture's count < 60% of the trailing median (partial render)


def _disease_area(program_raw: dict) -> str | None:
    for fld in program_raw.get("additional_fields", []):
        if "area" in fld.get("name", "").lower():
            return fld.get("value")
    return None


def _quarantine_flags(totals: list[int]) -> list[bool]:
    """A capture is quarantined if its count collapses vs the trailing median of accepted captures."""
    accepted: list[int] = []
    flags = []
    for i, n in enumerate(totals):
        trail = sorted(accepted[-4:])
        med = trail[len(trail) // 2] if trail else n
        q = i > 0 and 0 < n < DROP * med
        flags.append(q)
        if not q:
            accepted.append(n)
    return flags


def _program_periods(s: Session, company_id: str, origins: tuple[str, ...]) -> list[dict]:
    rows = s.execute(
        select(Snapshot, Extraction)
        .join(Extraction, Extraction.snapshot_id == Snapshot.snapshot_id)
        .join(CompanySource, CompanySource.source_id == Snapshot.source_id)
        .where(CompanySource.company_id == company_id, Snapshot.origin.in_(origins),
               Extraction.status != "failed", Extraction.raw_json.isnot(None))
        .order_by(func.coalesce(Snapshot.captured_at, Snapshot.fetched_at))
    ).all()
    out = []
    for snap, ext in rows:
        result = ExtractionResult.model_validate(ext.raw_json)
        raw = ext.raw_json
        programs = []
        for ea, ea_raw in zip(result.assets, raw["assets"], strict=False):
            for ep, ep_raw in zip(ea.programs, ea_raw["programs"], strict=False):
                programs.append({
                    "phase_code": normalize_phase(s, ep.phase_verbatim),
                    "ta": disease_area_to_ta(_disease_area(ep_raw), ep.indication_verbatim),
                })
        captured = snap.captured_at or snap.fetched_at
        out.append({
            "period": (snap.render_meta or {}).get("quarter") or captured.date().isoformat(),
            "captured_at": captured.date().isoformat(),
            "source_url": (snap.render_meta or {}).get("archive_url"),
            "programs": programs,
        })
    return out


def period_distribution(s: Session, company_id: str, dim: str,
                        origins: tuple[str, ...] = ("wayback",)) -> dict:
    """dim ∈ {phase, therapeutic_area}. Per-quarter PROGRAM counts by bucket, quarantined quarters
    flagged. `unit` is returned so the UI can label the axis honestly."""
    periods = _program_periods(s, company_id, origins)
    flags = _quarantine_flags([len(p["programs"]) for p in periods])
    quarters, bucket_set = [], set()
    for p, q in zip(periods, flags, strict=False):
        counts: Counter = Counter()
        for prog in p["programs"]:
            if dim == "phase":
                code = prog["phase_code"] or "unmapped"
                counts[PHASE_LABELS.get(code, code)] += 1
            else:
                counts[prog["ta"]] += 1
        bucket_set |= set(counts)
        quarters.append({"period": p["period"], "captured_at": p["captured_at"],
                         "source_url": p["source_url"], "quarantined": q,
                         "total": len(p["programs"]), "counts": dict(counts)})
    if dim == "phase":
        order = list(PHASE_LABELS.values()) + ["unmapped"]
        buckets = sorted(bucket_set, key=lambda b: order.index(b) if b in order else 99)
    else:
        tot: Counter = Counter()
        for q in quarters:
            tot.update(q["counts"])
        buckets = [b for b, _ in tot.most_common()]
    return {"company_id": company_id, "dim": dim, "unit": "programs", "buckets": buckets,
            "quarters": quarters}


def _compound_size_by_quarter(s: Session, company_id: str, origins: tuple[str, ...]) -> list[dict]:
    """Distinct-compound (asset) count per quarter, with quarantine flags — the flow-chart baseline."""
    captures, _ = _captures_for_company(s, company_id, origins)
    flags = _quarantine_flags([len(c.assets) for c in captures])
    return [{"period": c.period, "captured_at": c.captured_at.isoformat(),
             "pipeline_size": len(c.assets), "quarantined": q}
            for c, q in zip(captures, flags, strict=False)]


def change_events(s: Session, company_id: str, types: list[str] | None = None,
                  status: str | None = "confirmed") -> list[dict]:
    """The change-event feed for a company (joined to asset names), newest change first."""
    conds = [ChangeEvent.company_id == company_id]
    if status:
        conds.append(ChangeEvent.status == status)
    if types:
        conds.append(ChangeEvent.event_type.in_(types))
    rows = s.execute(
        select(ChangeEvent, Asset.preferred_name)
        .join(Asset, Asset.asset_id == ChangeEvent.asset_id, isouter=True)
        .where(*conds)
        .order_by(ChangeEvent.eff_max.desc().nullslast(), ChangeEvent.event_id)
    ).all()
    return [{
        "event_type": e.event_type, "asset": name, "asset_id": e.asset_id, "period": e.period,
        "from_phase": e.from_phase, "to_phase": e.to_phase, "direction": e.direction,
        "last_phase": e.last_phase, "exit_class": e.exit_class, "partner": e.partner,
        "eff_min": e.eff_min.date().isoformat() if e.eff_min else None,
        "eff_max": e.eff_max.date().isoformat() if e.eff_max else None,
        "status": e.status,
    } for e, name in rows]


def history_summary(s: Session, company_id: str, origins: tuple[str, ...] = ("wayback",)) -> dict:
    """Headline metrics. Exits are split by class — 'discontinued_confirmed' counts ONLY
    likely_discontinued_early; approvals/late-stage exits are reported separately, never conflated."""
    events = change_events(s, company_id, status="confirmed")
    size = _compound_size_by_quarter(s, company_id, origins)

    per_q: dict[str, dict] = {q["period"]: {"period": q["period"], "added": 0, "discontinued": 0,
                                            "exit_approved_or_late": 0, "advances": 0} for q in size}
    for e in events:
        row = per_q.setdefault(e["period"], {"period": e["period"], "added": 0, "discontinued": 0,
                                             "exit_approved_or_late": 0, "advances": 0})
        if e["event_type"] == "asset_added":
            row["added"] += 1
        elif e["event_type"] == "asset_phase_changed" and e["direction"] == "advance":
            row["advances"] += 1
        elif e["event_type"] == "asset_left_pipeline":
            early = e["exit_class"] == "likely_discontinued_early"
            row["discontinued" if early else "exit_approved_or_late"] += 1

    exits = [e for e in events if e["event_type"] == "asset_left_pipeline"]
    advances = [e for e in events
                if e["event_type"] == "asset_phase_changed" and e["direction"] == "advance"]
    partners = [e for e in events if e["event_type"] in ("partner_added", "partner_removed")]
    return {
        "company_id": company_id,
        "pipeline_size_by_quarter": size,
        "per_quarter": [per_q[k] for k in sorted(per_q)],
        "totals": {
            "assets_added": sum(1 for e in events if e["event_type"] == "asset_added"),
            "phase_advances": len(advances),
            "discontinued_confirmed": sum(1 for e in exits
                                          if e["exit_class"] == "likely_discontinued_early"),
            "exits_approved_or_late": sum(1 for e in exits
                                          if e["exit_class"] != "likely_discontinued_early"),
            "partner_changes": len(partners),
        },
        "caveat": ("Exits are classified, not assumed discontinued: a drug leaving the page may be "
                   "approved-and-graduated, discontinued, or renamed. 'discontinued_confirmed' counts "
                   "only early-phase exits; later-stage exits need an external approval signal."),
    }
