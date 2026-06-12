"""Backfill targets, modality, and mechanism from the Open Targets Platform (EBI +
Wellcome Sanger) for assets where the company didn't disclose them.

Most pipeline pages list drug + indication + phase but not the molecular target or
modality. Open Targets links each drug (by ChEMBL ID) to its target gene (HGNC symbol),
drug type, and mechanism of action — so we resolve each asset to a ChEMBL drug and fill
the gaps. Everything backfilled is provenance-tagged source='open_targets', never
overwriting company-disclosed values.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline_intel.gold.models import Asset, AssetSynonym, AssetTarget, Target

OT_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"

# Open Targets drugType -> our modality vocabulary code.
_DRUGTYPE_TO_MODALITY = {
    "antibody": "mab",
    "antibody drug conjugate": "adc",
    "small molecule": "small_molecule",
    "protein": "protein",
    "enzyme": "protein",
    "oligonucleotide": "rna",
    "oligosaccharide": "other",
    "cell": "cell_therapy",
    "gene therapy": "gene_therapy",
    "radiotherapy": "radioligand",
    "unknown": None,
}

# A mechanism listing more targets than this is a gene FAMILY (e.g. an ADC's tubulin
# payload spans ~15 TUBB/TUBA genes) — that's the conjugate's cytotoxic mechanism, not the
# drug's therapeutic target. We keep specific mechanisms (incl. bispecifics' 2 targets).
_MAX_TARGETS_PER_MECHANISM = 4

_SEARCH_Q = "query($q:String!){ search(queryString:$q, entityNames:[\"drug\"]){ hits{ id name } } }"
_DRUG_Q = """query($id:String!){ drug(chemblId:$id){
  name drugType
  mechanismsOfAction{ rows{ mechanismOfAction actionType targets{ approvedSymbol approvedName } } }
} }"""


@dataclass
class DrugAnnotation:
    chembl_id: str
    drug_type: str | None
    targets: list[tuple[str, str]] = field(default_factory=list)  # (symbol, name)
    action_type: str | None = None
    mechanism: str | None = None


def _post(query: str, variables: dict) -> dict:
    with httpx.Client(timeout=25.0) as c:
        r = c.post(OT_GRAPHQL, json={"query": query, "variables": variables})
        r.raise_for_status()
        return r.json().get("data", {}) or {}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def resolve_drug(name: str) -> str | None:
    """Resolve a drug name/code to its ChEMBL ID via Open Targets search."""
    hits = _post(_SEARCH_Q, {"q": name}).get("search", {}).get("hits", [])
    return hits[0]["id"] if hits else None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def drug_annotation(chembl_id: str) -> DrugAnnotation | None:
    d = _post(_DRUG_Q, {"id": chembl_id}).get("drug")
    if not d:
        return None
    rows = (d.get("mechanismsOfAction") or {}).get("rows", []) or []
    targets, action, moa = [], None, None
    for row in rows:
        moa = moa or row.get("mechanismOfAction")
        action = action or row.get("actionType")
        row_targets = row.get("targets", [])
        if len(row_targets) > _MAX_TARGETS_PER_MECHANISM:
            continue  # gene-family / payload mechanism — not the therapeutic target
        for t in row_targets:
            sym = t.get("approvedSymbol")
            if sym and (sym, t.get("approvedName", "")) not in targets:
                targets.append((sym, t.get("approvedName", "")))
    return DrugAnnotation(chembl_id=chembl_id, drug_type=d.get("drugType"),
                          targets=targets, action_type=action, mechanism=moa)


@dataclass
class BackfillStats:
    assets_seen: int = 0
    resolved: int = 0
    targets_added: int = 0
    modality_filled: int = 0
    mechanism_filled: int = 0
    unresolved: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _resolve_asset(s: Session, asset: Asset) -> str | None:
    """Try the preferred name, then synonyms, against Open Targets drug search."""
    names = [asset.preferred_name]
    names += list(s.execute(
        select(AssetSynonym.synonym).where(AssetSynonym.asset_id == asset.asset_id)
    ).scalars())
    seen = set()
    for n in names:
        key = n.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        cid = resolve_drug(n)
        if cid:
            return cid
    return None


def _link_target(s: Session, asset_id: str, symbol: str, name: str, action: str | None) -> bool:
    target = s.execute(
        select(Target).where(Target.hgnc_symbol == symbol)
    ).scalar_one_or_none()
    if target is None:
        target = Target(hgnc_symbol=symbol, name=name or symbol)
        s.add(target)
        s.flush()
    exists = s.execute(
        select(AssetTarget).where(AssetTarget.asset_id == asset_id, AssetTarget.target_id == target.target_id)
    ).scalar_one_or_none()
    if exists is not None:
        return False
    s.add(AssetTarget(asset_id=asset_id, target_id=target.target_id, verbatim=symbol,
                      action=action, source="open_targets"))
    return True


def enrich_asset(s: Session, asset: Asset, stats: BackfillStats) -> None:
    if asset.chembl_id:
        cid = asset.chembl_id
    else:
        cid = _resolve_asset(s, asset)
    if not cid:
        stats.unresolved += 1
        return
    ann = drug_annotation(cid)
    if ann is None:
        stats.unresolved += 1
        return

    stats.resolved += 1
    asset.chembl_id = cid

    for sym, name in ann.targets:
        if _link_target(s, asset.asset_id, sym, name, ann.action_type):
            stats.targets_added += 1

    # Modality: only fill if the company didn't disclose one.
    if not asset.modality_code and ann.drug_type:
        code = _DRUGTYPE_TO_MODALITY.get(ann.drug_type.strip().lower())
        if code:
            asset.modality_code = code
            asset.modality_verbatim = ann.drug_type
            asset.modality_source = "open_targets"
            stats.modality_filled += 1

    # Mechanism into extras (clearly tagged as enriched).
    if ann.mechanism:
        extras = dict(asset.extras or {})
        if "mechanism (Open Targets)" not in extras:
            extras["mechanism (Open Targets)"] = ann.mechanism
            asset.extras = extras
            stats.mechanism_filled += 1


def backfill_all(s: Session, limit: int | None = None, commit_every: int = 15) -> BackfillStats:
    stats = BackfillStats()
    q = select(Asset)
    if limit:
        q = q.limit(limit)
    assets = list(s.execute(q).scalars())
    for i, asset in enumerate(assets, 1):
        stats.assets_seen += 1
        enrich_asset(s, asset, stats)
        if i % commit_every == 0:
            s.commit()
    s.commit()
    return stats
