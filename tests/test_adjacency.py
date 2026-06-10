"""DB-backed tests for ontology-adjacency search: closure correctness and
descendant/ancestor expansion. Builds a tiny synthetic disease tree (no network)."""

import uuid

import pytest

from pipeline_intel.gold.models import (
    Asset,
    Company,
    Indication,
    IndicationMapping,
    OntologyEdge,
    OntologyTerm,
    Program,
    ProgramVersion,
)
from pipeline_intel.ontology.closure import rebuild_closure
from pipeline_intel.search import facets


@pytest.fixture
def tree(tx):
    """cancer (A) -> lung cancer (B) -> NSCLC (C); plus melanoma (M) under cancer."""
    u = uuid.uuid4().hex[:6]
    A, B, C, M = (f"T{u}:A", f"T{u}:B", f"T{u}:C", f"T{u}:M")
    for cur, lab in [(A, "cancer"), (B, "lung cancer"), (C, "NSCLC"), (M, "melanoma")]:
        tx.add(OntologyTerm(curie=cur, ontology=f"T{u}", label=lab, synonyms=[]))
    tx.add(OntologyEdge(parent_curie=A, child_curie=B))
    tx.add(OntologyEdge(parent_curie=B, child_curie=C))
    tx.add(OntologyEdge(parent_curie=A, child_curie=M))
    tx.flush()
    rebuild_closure(tx)
    return {"A": A, "B": B, "C": C, "M": M}


def test_adjacent_descendants_and_ancestors(tx, tree):
    # From NSCLC (leaf): ancestors lung cancer (1), cancer (2)
    adj = facets.adjacent_curies(tx, tree["C"], max_ancestor_hops=2)
    assert adj[tree["C"]]["relation"] == "exact"
    assert adj[tree["B"]] == {"relation": "ancestor", "distance": 1}
    assert adj[tree["A"]] == {"relation": "ancestor", "distance": 2}

    # From cancer (root): descendants lung cancer (1), NSCLC (2), melanoma (1)
    adj = facets.adjacent_curies(tx, tree["A"], max_ancestor_hops=2)
    assert adj[tree["B"]] == {"relation": "descendant", "distance": 1}
    assert adj[tree["C"]] == {"relation": "descendant", "distance": 2}
    assert adj[tree["M"]] == {"relation": "descendant", "distance": 1}


def test_ancestor_hop_limit(tx, tree):
    # cancer is 2 hops above NSCLC; with max_up=1 it should be excluded
    adj = facets.adjacent_curies(tx, tree["C"], max_ancestor_hops=1)
    assert tree["B"] in adj
    assert tree["A"] not in adj


def test_programs_by_indication_expands_to_subtypes(tx, tree):
    u = uuid.uuid4().hex[:6]
    company = Company(name=f"AdjCo-{u}")
    tx.add(company)
    tx.flush()
    asset = Asset(preferred_name=f"DRUG-{u}", extras={})
    tx.add(asset)
    tx.flush()
    ind = Indication(preferred_label=f"NSCLC-{u}")
    tx.add(ind)
    tx.flush()
    # NSCLC indication maps to the leaf term C
    tx.add(IndicationMapping(indication_id=ind.indication_id, ontology="T", curie=tree["C"],
                             label="NSCLC", confidence=0.95, method="exact", status="auto"))
    prog = Program(asset_id=asset.asset_id, indication_id=ind.indication_id,
                   company_id=company.company_id)
    tx.add(prog)
    tx.flush()
    tx.add(ProgramVersion(program_id=prog.program_id, phase_code="phase_2", phase_verbatim="Phase 2",
                          status="active", indication_verbatim="NSCLC"))
    tx.flush()

    # Querying the PARENT disease (lung cancer) should surface the NSCLC program as a descendant.
    res = facets.programs_by_indication(tx, tree["B"], max_ancestor_hops=2)
    assert len(res["results"]) == 1
    row = res["results"][0]
    assert row["asset_name"] == f"DRUG-{u}"
    assert row["relation"] == "descendant" and row["distance"] == 1
