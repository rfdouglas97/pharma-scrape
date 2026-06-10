"""Build the EFO is-a graph for the diseases in our data, and recompute the transitive
closure that powers indication-adjacency search.

Incremental: BFS upward (via OLS direct-parents) only from mapped CURIEs not already in
ontology_term, accumulating terms + edges. Then the closure is rebuilt in one recursive
SQL pass over all edges, so it's always consistent with the current graph. Re-runs after
new mappings are cheap (only new terms are fetched from OLS).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import IndicationMapping, OntologyEdge, OntologyTerm
from pipeline_intel.ontology import ols_client

_OLS_DELAY = 0.1  # OLS is a public API; polite but not as slow as page scraping
# Only recurse up the disease backbone — HP/DOID/Orphanet cross-links explode the walk.
_WALK_PREFIXES = ("MONDO:", "EFO:")
_MAX_TERMS = 4000  # hard cap so a pathological graph can't run forever
# Don't climb past N hops above a mapped disease — adjacency only needs nearby super-types,
# not the whole chain up to generic roots (which makes the walk explore most of MONDO).
_MAX_DEPTH = 6
# Ultra-generic roots: record the edge but don't expand above them (nothing useful up there).
_STOP_CURIES = {
    "MONDO:0000001",   # disease
    "MONDO:0700096",   # human disease
    "MONDO:0021199",   # disease by anatomical system
    "EFO:0000408",     # disease
    "EFO:0000651",     # phenotype
    "OGMS:0000031",    # disease (upper)
}


@dataclass
class ClosureStats:
    seeds: int = 0
    new_terms: int = 0
    new_edges: int = 0
    closure_rows: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _mapped_curies(s: Session) -> list[str]:
    return list(s.execute(
        select(IndicationMapping.curie).where(
            IndicationMapping.curie.isnot(None),
            IndicationMapping.status.in_(("auto", "reviewed")),
        ).distinct()
    ).scalars())


def build_graph(s: Session) -> ClosureStats:
    stats = ClosureStats()
    seeds = _mapped_curies(s)
    stats.seeds = len(seeds)

    known = set(s.execute(select(OntologyTerm.curie)).scalars())

    queue = [(c, 0) for c in seeds if c not in known]  # (curie, depth above a seed)
    visited: set[str] = set()
    processed = 0

    while queue and len(visited) < _MAX_TERMS:
        curie, depth = queue.pop()
        if curie in visited:
            continue
        visited.add(curie)

        term = ols_client.term_by_curie(curie)
        if term is None or not term.iri:
            continue
        if curie not in known:
            s.add(OntologyTerm(curie=curie, ontology=curie.split(":")[0], label=term.label, synonyms=[]))
            known.add(curie)
            stats.new_terms += 1
        if curie in _STOP_CURIES or depth >= _MAX_DEPTH:
            continue  # don't expand above ultra-generic roots or past the depth cap

        for parent in ols_client.direct_parents(term.iri):
            res = s.execute(
                pg_insert(OntologyEdge)
                .values(parent_curie=parent.curie, child_curie=curie)
                .on_conflict_do_nothing()
            )
            if res.rowcount:
                stats.new_edges += 1
            if parent.curie not in known:
                s.add(OntologyTerm(curie=parent.curie, ontology=parent.curie.split(":")[0],
                                   label=parent.label, synonyms=[]))
                known.add(parent.curie)
                stats.new_terms += 1
            # Only recurse up the disease backbone; skip stop-roots; obey the depth cap.
            if (parent.curie.startswith(_WALK_PREFIXES) and parent.curie not in _STOP_CURIES
                    and parent.curie not in visited):
                queue.append((parent.curie, depth + 1))

        processed += 1
        if processed % 25 == 0:
            s.commit()  # durable, resumable progress
        time.sleep(_OLS_DELAY)

    s.commit()
    stats.closure_rows = rebuild_closure(s)
    s.commit()
    return stats


def rebuild_closure(s: Session) -> int:
    """Recompute ontology_closure from ontology_edge in one recursive pass (ancestor ->
    descendant with minimum hop distance; includes self-pairs at depth 0)."""
    s.execute(text("TRUNCATE ontology_closure"))
    s.execute(text("""
        INSERT INTO ontology_closure (ancestor_curie, descendant_curie, depth)
        WITH RECURSIVE walk(ancestor_curie, descendant_curie, depth) AS (
            SELECT curie, curie, 0 FROM ontology_term
            UNION ALL
            SELECT e.parent_curie, w.descendant_curie, w.depth + 1
            FROM ontology_edge e
            JOIN walk w ON e.child_curie = w.ancestor_curie
            WHERE w.depth < 30
        )
        SELECT ancestor_curie, descendant_curie, MIN(depth)
        FROM walk
        GROUP BY ancestor_curie, descendant_curie
    """))
    return s.execute(text("SELECT count(*) FROM ontology_closure")).scalar_one()
