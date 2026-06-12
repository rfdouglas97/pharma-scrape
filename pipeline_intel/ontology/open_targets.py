"""Backfill the molecular TARGET from the Open Targets Platform (EBI + Wellcome Sanger)
— and ONLY for assets where the company disclosed no target/mechanism at all.

The company's own disclosure always wins. If a page discloses a mode of action (e.g.
GSK's "Mode of Action" column: "Ileal bile acid transporter inhibitor", "anti-IL5
antibody"), that IS the disclosed target/mechanism and we leave it alone — Open Targets
must not overwrite it or take credit for it. We deliberately do NOT derive modality from
anything here: modality is only set when the company explicitly states it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline_intel.gold.models import Asset, AssetSynonym, AssetTarget, Target

OT_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"

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
    skipped_disclosed: int = 0  # company already disclosed a target/mechanism
    resolved: int = 0
    targets_added: int = 0
    unresolved: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _has_disclosed_target(s: Session, asset: Asset) -> bool:
    """True if the company disclosed a mechanism/MoA or a target for this asset."""
    if asset.mechanism_verbatim:
        return True
    return s.execute(
        select(AssetTarget.id).where(
            AssetTarget.asset_id == asset.asset_id, AssetTarget.source == "disclosed"
        ).limit(1)
    ).scalar_one_or_none() is not None


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
    # The company's disclosure wins — only fill genuine gaps.
    if _has_disclosed_target(s, asset):
        stats.skipped_disclosed += 1
        return

    cid = asset.chembl_id or _resolve_asset(s, asset)
    if not cid:
        stats.unresolved += 1
        return
    ann = drug_annotation(cid)
    if ann is None or not ann.targets:
        stats.unresolved += 1
        return

    stats.resolved += 1
    asset.chembl_id = cid
    for sym, name in ann.targets:
        if _link_target(s, asset.asset_id, sym, name, ann.action_type):
            stats.targets_added += 1
    # NOTE: we deliberately do NOT backfill modality or mechanism — only the target gene,
    # and only because the company disclosed neither.


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
