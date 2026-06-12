"""Pure change-detection core — the scalable heart of the longitudinal feed.

This module is the generalized, DB-free version of the BMS proof-of-concept. It takes a
chronologically ordered list of captures whose assets are ALREADY resolved to stable ids and
ALREADY phase-normalized, and emits the change-event stream. Identity resolution and phase
normalization (the hard, decision-store-backed parts) happen in the caller (`rebuild.py`) — this
core only does timeline logic, which is exactly what lets it scale and be unit-tested without a DB.

Scaling properties:
- O(assets) work per capture per company → linear in total observations.
- Discontinuation = "missing in the next capture(s)": a set-difference between consecutive captures,
  with a K-capture confirmation window so one bad/partial capture can't fake a discontinuation.
- A drug LEAVING the page is three-way ambiguous (approved-and-graduated / discontinued / renamed),
  so exits are classified by exit phase rather than asserted to be discontinuations; full
  disambiguation needs an external approval signal (handled upstream, not here).
- A bad-capture guard quarantines a capture whose asset count collapses vs the trailing median
  (partial render / extraction miss) — it is carried forward, never diffed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Default phase ranking, mirroring config/vocab/phase.yaml sort_order. Override via detect_changes.
PHASE_ORDER: dict[str, int] = {
    "preclinical": 10, "phase_1": 20, "phase_1_2": 25, "phase_2": 30, "phase_2_3": 35,
    "phase_3": 40, "filed": 50, "approved": 60, "discontinued": 70,
}


@dataclass(frozen=True)
class AssetObs:
    """One asset as observed in one capture (already resolved + normalized)."""
    asset_id: str
    name: str
    phase_code: str | None                 # normalized vocab code, or None if unmapped
    indications: frozenset[str] = frozenset()
    partners: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Capture:
    period: str                            # label, e.g. "2021Q1"
    captured_at: date                      # temporal anchor (Wayback capture date / fetch date)
    assets: tuple[AssetObs, ...]


@dataclass
class ChangeEvent:
    type: str                              # asset_added | asset_left_pipeline | asset_phase_changed
                                           # | partner_added | partner_removed | asset_reappeared
    asset_id: str
    asset: str
    period: str
    eff_min: date | None                   # last date the prior state was seen (interval start)
    eff_max: date | None                   # first date the change was observed (interval end)
    status: str = "confirmed"              # confirmed | provisional
    # type-specific:
    from_phase: str | None = None
    to_phase: str | None = None
    direction: str | None = None           # advance | regress | lateral
    last_phase: str | None = None
    exit_class: str | None = None          # for asset_left_pipeline
    partner: str | None = None


def exit_class(phase_code: str | None, order: dict[str, int]) -> str:
    """Best available proxy for what a disappearance means, given exit phase."""
    if phase_code in ("filed", "approved"):
        return "likely_approved_graduated"
    if phase_code == "phase_3":
        return "late_stage_exit_ambiguous"        # approval OR Phase 3 failure — needs external signal
    return "likely_discontinued_early"            # Phase 1/2/preclinical exit -> usually deprioritized


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


@dataclass
class _State:
    obs: AssetObs
    last_seen: date
    open: bool = True
    absent_streak: int = 0
    first_absent_period: str | None = None
    first_absent_date: date | None = None


def detect_changes(
    captures: list[Capture],
    *,
    confirm_n: int = 2,
    drop_threshold: float = 0.6,
    phase_order: dict[str, int] | None = None,
) -> tuple[list[ChangeEvent], list[dict]]:
    """Replay captures oldest->newest, emitting the change-event stream.

    Returns (events, quarantined). `quarantined` lists captures dropped by the bad-capture guard.
    The first capture is the cold-start baseline (its membership is initial state, not events).
    """
    order = phase_order or PHASE_ORDER
    events: list[ChangeEvent] = []
    quarantined: list[dict] = []
    if not captures:
        return events, quarantined

    state: dict[str, _State] = {}
    base = captures[0]
    for o in base.assets:
        state[o.asset_id] = _State(obs=o, last_seen=base.captured_at)
    accepted_counts = [len(base.assets)]

    for cap in captures[1:]:
        cur = {o.asset_id: o for o in cap.assets}

        # bad-capture guard: collapse vs trailing median -> carry forward, no diff.
        trail = _median(accepted_counts[-4:])
        if 0 < len(cur) < drop_threshold * trail:
            quarantined.append({"period": cap.period, "assets": len(cur), "trail_median": trail})
            continue
        accepted_counts.append(len(cur))

        # additions / phase changes / reappearances / partner diffs
        for aid, o in cur.items():
            st = state.get(aid)
            if st is None:
                events.append(ChangeEvent("asset_added", aid, o.name, cap.period, None,
                                          cap.captured_at, to_phase=_label(o.phase_code)))
                state[aid] = _State(obs=o, last_seen=cap.captured_at)
                continue
            if not st.open:
                events.append(ChangeEvent("asset_reappeared", aid, o.name, cap.period,
                                          st.last_seen, cap.captured_at))
                st.obs, st.last_seen, st.open, st.absent_streak = o, cap.captured_at, True, 0
                continue
            if o.phase_code != st.obs.phase_code and o.phase_code is not None:
                old, new = order.get(st.obs.phase_code, 0), order.get(o.phase_code, 0)
                events.append(ChangeEvent(
                    "asset_phase_changed", aid, o.name, cap.period, st.last_seen, cap.captured_at,
                    from_phase=_label(st.obs.phase_code), to_phase=_label(o.phase_code),
                    direction="advance" if new > old else ("regress" if new < old else "lateral")))
            for p in o.partners - st.obs.partners:
                events.append(ChangeEvent("partner_added", aid, o.name, cap.period, None,
                                          cap.captured_at, partner=p))
            for p in st.obs.partners - o.partners:
                events.append(ChangeEvent("partner_removed", aid, o.name, cap.period, None,
                                          cap.captured_at, partner=p))
            st.obs, st.last_seen, st.absent_streak = o, cap.captured_at, 0

        # disappearances: confirmation window + interval dates + exit classification
        for aid, st in state.items():
            if not st.open or aid in cur:
                continue
            st.absent_streak += 1
            if st.absent_streak == 1:
                st.first_absent_period, st.first_absent_date = cap.period, cap.captured_at
            if st.absent_streak >= confirm_n:
                events.append(ChangeEvent(
                    "asset_left_pipeline", aid, st.obs.name, st.first_absent_period,
                    st.last_seen, st.first_absent_date, last_phase=_label(st.obs.phase_code),
                    exit_class=exit_class(st.obs.phase_code, order)))
                st.open = False

    # trailing provisional exits (disappeared but not yet confirmed at series end)
    for aid, st in state.items():
        if st.open and 0 < st.absent_streak < confirm_n:
            events.append(ChangeEvent(
                "asset_left_pipeline", aid, st.obs.name, st.first_absent_period,
                st.last_seen, st.first_absent_date, status="provisional",
                last_phase=_label(st.obs.phase_code), exit_class=exit_class(st.obs.phase_code, order)))

    return events, quarantined


_LABELS = {
    "preclinical": "Preclinical", "phase_1": "Phase 1", "phase_1_2": "Phase 1/2", "phase_2": "Phase 2",
    "phase_2_3": "Phase 2/3", "phase_3": "Phase 3", "filed": "Filed / Registration",
    "approved": "Approved / Marketed", "discontinued": "Discontinued",
}


def _label(code: str | None) -> str | None:
    return _LABELS.get(code, code)
