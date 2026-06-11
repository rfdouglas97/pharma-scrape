"""EMBL-EBI Ontology Lookup Service (OLS4) client for EFO.

We anchor indications to EFO; EFO's disease branch incorporates MONDO IDs, so an EFO
lookup returns EFO:* or MONDO:* CURIEs — both are valid disease anchors (this is the
"EFO primary + MONDO crosswalk" the plan calls for). We use two endpoints: term search
(label -> ranked candidates) and direct parents (to build the is-a edge graph).
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

OLS_BASE = "https://www.ebi.ac.uk/ols4/api"
# Accept only true DISEASE ontologies — HP (phenotypes) and DOID are excluded so we never
# anchor an indication to a phenotype term (whose hierarchy has no disease categories).
DISEASE_PREFIXES = ("MONDO:", "EFO:", "Orphanet:")
_TIMEOUT = httpx.Timeout(20.0)


def _ontology_for(curie_or_iri: str) -> str:
    """Resolve a term in its NATIVE ontology — MONDO terms aren't all in EFO's slim
    (e.g. some infectious diseases), so EFO-only lookups miss them."""
    return "mondo" if "MONDO" in curie_or_iri else "efo"


@dataclass
class TermCandidate:
    curie: str
    label: str
    iri: str
    score: float | None = None


_UA = "PipelineIntelBot/0.1 (ontology enrichment)"


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA})


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def search_disease(
    label: str, rows: int = 5, exact: bool = False, ontology: str = "mondo"
) -> list[TermCandidate]:
    """Search a disease ontology (MONDO by default — pure diseases, no cell lines/
    experimental factors that pollute EFO search) for a label. Returns ranked candidates."""
    params = {
        "q": label,
        "ontology": ontology,
        "rows": rows,
        "exact": str(exact).lower(),
        "fieldList": "obo_id,label,iri,score",
        "type": "class",
    }
    if exact:
        # Match the disease's primary label OR its synonyms exactly — this reliably surfaces
        # the BASE term (e.g. 'Alzheimer disease', 'COVID-19') that fuzzy ranking buries under
        # numbered subtypes.
        params["queryFields"] = "label,synonym"
    with _client() as c:
        r = c.get(f"{OLS_BASE}/search", params=params)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    out: list[TermCandidate] = []
    for d in docs:
        curie = d.get("obo_id")
        if not curie or not curie.startswith(DISEASE_PREFIXES):
            continue
        out.append(TermCandidate(curie=curie, label=d.get("label", ""), iri=d.get("iri", ""),
                                 score=d.get("score")))
    return out


def best_exact(label: str) -> TermCandidate | None:
    """Exact (case-insensitive label) match if one exists — highest-confidence path."""
    for c in search_disease(label, rows=5, exact=True):
        if c.label.strip().lower() == label.strip().lower():
            return c
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def term_by_curie(curie: str) -> TermCandidate | None:
    """Resolve a CURIE to its term (with IRI) — needed to walk parents from a stored mapping."""
    onto = _ontology_for(curie)
    with _client() as c:
        r = c.get(f"{OLS_BASE}/ontologies/{onto}/terms", params={"obo_id": curie, "size": 1})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        terms = r.json().get("_embedded", {}).get("terms", [])
    if not terms:
        return None
    t = terms[0]
    return TermCandidate(curie=t.get("obo_id") or curie, label=t.get("label", ""), iri=t.get("iri", ""))


def _encode_iri(iri: str) -> str:
    return urllib.parse.quote(urllib.parse.quote(iri, safe=""), safe="")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def hierarchical_ancestors(iri: str) -> list[str]:
    """All is-a ancestors of a term (full chain to the roots), as CURIEs. Used for
    therapeutic-area rollup — guaranteed to reach top-level categories regardless of depth."""
    frag = _encode_iri(iri)
    onto = _ontology_for(iri)
    with _client() as c:
        r = c.get(f"{OLS_BASE}/ontologies/{onto}/terms/{frag}/hierarchicalAncestors", params={"size": 200})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        terms = r.json().get("_embedded", {}).get("terms", [])
    return [t["obo_id"] for t in terms if t.get("obo_id")]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def direct_parents(iri: str) -> list[TermCandidate]:
    """Direct is-a parents of a term (one hop up). Builds the edge graph; depth comes from
    the closure CTE. Disease-ontology terms only — drops BFO/upper-ontology roots."""
    frag = _encode_iri(iri)
    onto = _ontology_for(iri)
    with _client() as c:
        r = c.get(f"{OLS_BASE}/ontologies/{onto}/terms/{frag}/hierarchicalParents", params={"size": 50})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        terms = r.json().get("_embedded", {}).get("terms", [])
    out: list[TermCandidate] = []
    for t in terms:
        curie = t.get("obo_id")
        if curie and curie.startswith(DISEASE_PREFIXES):
            out.append(TermCandidate(curie=curie, label=t.get("label", ""), iri=t.get("iri", "")))
    return out
