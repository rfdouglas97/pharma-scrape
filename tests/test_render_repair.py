from pipeline_intel.ingest.render import (
    GENERIC_DISMISS_SELECTORS,
    GENERIC_EXPAND_SELECTORS,
    repair_render_config,
)


def test_repair_config_escalates_from_empty():
    cfg = repair_render_config(None)
    assert cfg["wait_ms"] >= 3500
    assert cfg["full_page"] is True
    assert cfg["scroll"] is True
    assert set(GENERIC_DISMISS_SELECTORS) <= set(cfg["dismiss_selectors"])
    assert set(GENERIC_EXPAND_SELECTORS) <= set(cfg["expand_selectors"])


def test_repair_config_preserves_and_merges_site_selectors():
    cfg = repair_render_config(
        {"wait_ms": 1000, "dismiss_selectors": ["#sitebanner"], "expand_selectors": ["#more"]}
    )
    # site selectors kept, generic ones appended, no duplicates
    assert cfg["dismiss_selectors"][0] == "#sitebanner"
    assert "#more" in cfg["expand_selectors"]
    assert set(GENERIC_EXPAND_SELECTORS) <= set(cfg["expand_selectors"])
    assert cfg["expand_selectors"].count(GENERIC_EXPAND_SELECTORS[0]) == 1


def test_repair_config_is_idempotent():
    once = repair_render_config({"repair_mode": True})
    twice = repair_render_config(once)
    assert twice["dismiss_selectors"] == once["dismiss_selectors"]
    assert twice["expand_selectors"] == once["expand_selectors"]
    assert twice["wait_ms"] == once["wait_ms"]


def test_repair_config_does_not_lower_existing_wait():
    cfg = repair_render_config({"wait_ms": 9000})
    assert cfg["wait_ms"] == 9000
