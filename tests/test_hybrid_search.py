"""Unit tests for the hybrid-search fusion logic (pure — no DB). The end-to-end structured/
lexical/vector path is exercised live against gold; here we lock the property that matters for a
quant consumer: reciprocal-rank fusion is deterministic and rewards agreement between signals."""

import pipeline_intel.search.hybrid as H


def test_rrf_fusion_deterministic_and_rewards_agreement(monkeypatch):
    ids = ["a", "b", "c", "d"]
    # a and b agree (same rank in both signals); c is lexical-only, d is vector-only.
    monkeypatch.setattr(H, "_lexical_rank", lambda s, q, i: ["a", "b", "c"])
    monkeypatch.setattr(H, "_vector_rank", lambda s, q, i: ["a", "b", "d"])

    out1 = H._fuse(None, "x", ids, semantic=True)
    out2 = H._fuse(None, "x", ids, semantic=True)
    assert out1 == out2  # same inputs -> identical order (reproducible backtests)
    assert out1[:2] == ["a", "b"]  # two-signal agreement takes the top slots
    # c (lexical rank2) and d (vector rank2) tie on fused score -> deterministic id tie-break.
    assert out1 == ["a", "b", "c", "d"]


def test_fuse_skips_vector_when_semantic_off(monkeypatch):
    monkeypatch.setattr(H, "_lexical_rank", lambda s, q, i: ["a", "b"])
    called = {"vector": False}

    def _vr(s, q, i):
        called["vector"] = True
        return ["b", "a"]

    monkeypatch.setattr(H, "_vector_rank", _vr)
    out = H._fuse(None, "x", ["a", "b", "c"], semantic=False)
    assert called["vector"] is False
    assert out[:2] == ["a", "b"]  # lexical order preserved
    assert out[2] == "c"  # unranked id sinks to the bottom


def test_phase_rank_orders_most_advanced_first():
    assert H._PHASE_RANK["approved"] > H._PHASE_RANK["phase_3"] > H._PHASE_RANK["phase_1"]
    assert H._PHASE_RANK["phase_1"] > H._PHASE_RANK["preclinical"]
