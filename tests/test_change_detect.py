"""Unit tests for the pure change-detection core (no DB).

These are the scalability/correctness proof for discontinuation-by-missingness: the confirmation
window stops one bad capture from faking a discontinuation, the bad-capture guard stops a partial
render from faking a mass discontinuation, and exits are classified rather than asserted.
"""

from __future__ import annotations

from datetime import date

from pipeline_intel.history.detect import AssetObs, Capture, detect_changes

# A stable background of assets present in every capture, so removing one test asset is a small
# drop (not a >40% collapse that the bad-capture guard would correctly quarantine). Real pipelines
# have 50+ assets; a single discontinuation is <10% drop. The guard targets partial renders.
_BG = [AssetObs(asset_id=f"bg{i}", name=f"bg{i}", phase_code="phase_2") for i in range(8)]


def _cap(period, d, *assets):
    return Capture(period=period, captured_at=d, assets=tuple(assets) + tuple(_BG))


def _a(aid, phase, name=None, partners=()):
    return AssetObs(asset_id=aid, name=name or aid, phase_code=phase, partners=frozenset(partners))


def _types(events):
    out = {}
    for e in events:
        out.setdefault(e.type, []).append(e)
    return out


def test_cold_start_baseline_emits_nothing():
    caps = [_cap("Q1", date(2021, 1, 1), _a("x", "phase_1"), _a("y", "phase_2"))]
    events, q = detect_changes(caps)
    assert events == [] and q == []


def test_addition_and_phase_advance():
    caps = [
        _cap("Q1", date(2021, 1, 1), _a("x", "phase_1")),
        _cap("Q2", date(2021, 4, 1), _a("x", "phase_2"), _a("y", "phase_1")),
    ]
    events, _ = detect_changes(caps)
    t = _types(events)
    assert [e.asset_id for e in t["asset_added"]] == ["y"]
    adv = t["asset_phase_changed"][0]
    assert adv.asset_id == "x" and adv.direction == "advance"
    assert adv.from_phase == "Phase 1" and adv.to_phase == "Phase 2"
    assert adv.eff_min == date(2021, 1, 1) and adv.eff_max == date(2021, 4, 1)


def test_discontinuation_requires_confirmation_window():
    # x present Q1, absent Q2 and Q3 -> confirmed at the 2nd absence (Q3), dated to first-absent (Q2)
    caps = [
        _cap("Q1", date(2021, 1, 1), _a("x", "phase_1"), _a("k", "phase_2")),
        _cap("Q2", date(2021, 4, 1), _a("k", "phase_2")),
        _cap("Q3", date(2021, 7, 1), _a("k", "phase_2")),
    ]
    events, _ = detect_changes(caps)
    left = [e for e in events if e.type == "asset_left_pipeline"]
    assert len(left) == 1
    e = left[0]
    assert e.asset_id == "x" and e.status == "confirmed"
    assert e.eff_min == date(2021, 1, 1) and e.eff_max == date(2021, 4, 1)  # interval, not a point


def test_single_absence_at_series_end_is_provisional():
    caps = [
        _cap("Q1", date(2021, 1, 1), _a("x", "phase_1"), _a("k", "phase_2")),
        _cap("Q2", date(2021, 4, 1), _a("k", "phase_2")),  # x absent once, series ends
    ]
    events, _ = detect_changes(caps)
    left = [e for e in events if e.type == "asset_left_pipeline"]
    assert len(left) == 1 and left[0].status == "provisional"


def test_bad_capture_guard_prevents_false_mass_discontinuation():
    # Q3 collapses to 1 of 5 assets (partial render). It must be quarantined and the 4 "missing"
    # assets must NOT be discontinued; Q4 restores them with no spurious events.
    def raw(p, d, *assets):  # no stable background — model a real collapse
        return Capture(period=p, captured_at=d, assets=tuple(assets))

    def full(p, d):
        return raw(p, d, _a("a", "phase_1"), _a("b", "phase_1"), _a("c", "phase_2"),
                   _a("d", "phase_2"), _a("e", "phase_3"))
    caps = [
        full("Q1", date(2021, 1, 1)),
        full("Q2", date(2021, 4, 1)),
        raw("Q3", date(2021, 7, 1), _a("a", "phase_1")),   # collapsed -> bad capture
        full("Q4", date(2021, 10, 1)),
    ]
    events, q = detect_changes(caps)
    assert len(q) == 1 and q[0]["period"] == "Q3"
    assert [e for e in events if e.type == "asset_left_pipeline"] == []
    assert [e for e in events if e.type == "asset_added"] == []  # nothing re-added either


def test_reappearance_reopens_and_flags():
    caps = [
        _cap("Q1", date(2021, 1, 1), _a("x", "phase_2"), _a("k", "phase_1")),
        _cap("Q2", date(2021, 4, 1), _a("k", "phase_1")),
        _cap("Q3", date(2021, 7, 1), _a("k", "phase_1")),                    # x confirmed left
        _cap("Q4", date(2021, 10, 1), _a("x", "phase_2"), _a("k", "phase_1")),  # x returns
    ]
    events, _ = detect_changes(caps)
    assert any(e.type == "asset_left_pipeline" and e.asset_id == "x" for e in events)
    assert any(e.type == "asset_reappeared" and e.asset_id == "x" for e in events)


def test_exit_classification_by_phase():
    caps = [
        _cap("Q1", date(2021, 1, 1), _a("early", "phase_1"), _a("late", "phase_3"), _a("k", "phase_1")),
        _cap("Q2", date(2021, 4, 1), _a("k", "phase_1")),
        _cap("Q3", date(2021, 7, 1), _a("k", "phase_1")),
    ]
    events, _ = detect_changes(caps)
    cls = {e.asset_id: e.exit_class for e in events if e.type == "asset_left_pipeline"}
    assert cls["early"] == "likely_discontinued_early"
    assert cls["late"] == "late_stage_exit_ambiguous"


def test_partner_changes_survive_but_renamed_asset_does_not_churn():
    # Same asset_id across captures despite a name change (resolution is the caller's job):
    # no add/remove, only the real partner handoff is emitted.
    caps = [
        _cap("Q1", date(2021, 1, 1), _a("x", "phase_3", name="liso-cel", partners=["Acme"])),
        _cap("Q2", date(2021, 4, 1), _a("x", "phase_3", name="BREYANZI", partners=["Beta"])),
    ]
    events, _ = detect_changes(caps)
    t = _types(events)
    assert "asset_added" not in t and "asset_left_pipeline" not in t  # rename did not churn
    assert {e.partner for e in t["partner_added"]} == {"Beta"}
    assert {e.partner for e in t["partner_removed"]} == {"Acme"}


def test_confirm_n_one_disables_window():
    caps = [
        _cap("Q1", date(2021, 1, 1), _a("x", "phase_1"), _a("k", "phase_2")),
        _cap("Q2", date(2021, 4, 1), _a("k", "phase_2")),
    ]
    events, _ = detect_changes(caps, confirm_n=1)
    left = [e for e in events if e.type == "asset_left_pipeline"]
    assert len(left) == 1 and left[0].status == "confirmed"
