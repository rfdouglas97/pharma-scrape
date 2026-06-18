from pipeline_intel.onboard import onboard_company


def test_onboard_stops_when_unresolved():
    # no pipeline URL found -> do not touch the DB, flag for human curation
    def resolve_fn():
        return {"pipeline_url": None, "method": None, "validated": False, "rationale": "unknown company"}

    out = onboard_company("Nonexistent Pharma", "ZZZZ", run=False, resolve_fn=resolve_fn)
    assert out["status"] == "unresolved"
    assert out["pipeline_url"] is None
    assert out.get("registered_company") is None
