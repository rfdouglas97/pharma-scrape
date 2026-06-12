"""One-time loader: ingest the BMS Wayback POC extractions into the DB so the longitudinal
history is queryable (and `change_event` populated), then rebuild the change feed.

Idempotent: snapshots are deduped by (source, captured_at, origin); `load_extraction` is itself
idempotent; aliases are skipped if present; `rebuild_history` rewrites the feed. Re-running is safe.

The curated alias map is seeded into `asset_alias` (the durable decision store) so rename-merge in
the rebuild matches the validated POC — the LLM clustering (`merge_assets.py`) is the later upgrade.
"""

from __future__ import annotations

import glob
import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pipeline_intel.extract.schemas import ExtractionResult
from pipeline_intel.gold.models import (
    Asset,
    AssetAlias,
    AssetSynonym,
    ChangeEvent,
    Company,
    CompanySource,
    Extraction,
    Snapshot,
)
from pipeline_intel.gold.upsert import load_extraction
from pipeline_intel.history.rebuild import rebuild_history

BMS_URL = "https://www.bms.com/researchers-and-partners/in-the-pipeline.html"


def _quality_score(result: ExtractionResult) -> float | None:
    progs = [p for a in result.assets for p in a.programs]
    if not progs:
        return 0.0
    ok = sum(bool(p.phase_verbatim and p.indication_verbatim) for p in progs)
    return round(ok / len(progs), 3)


def _company_id(s: Session, name: str) -> str:
    cid = s.execute(
        select(Company.company_id).where(Company.name.ilike(f"%{name}%")).limit(1)
    ).scalar_one_or_none()
    if cid:
        return cid
    co = Company(name=name)
    s.add(co)
    s.flush()
    return co.company_id


def _source_id(s: Session, company_id: str, url: str) -> str:
    sid = s.execute(
        select(CompanySource.source_id).where(
            CompanySource.company_id == company_id, CompanySource.url == url
        )
    ).scalar_one_or_none()
    if sid:
        return sid
    src = CompanySource(company_id=company_id, url=url, source_type="pipeline_page")
    s.add(src)
    s.flush()
    return src.source_id


def _asset_id_for_name(s: Session, name: str) -> str | None:
    return s.execute(
        select(AssetSynonym.asset_id).where(func.lower(AssetSynonym.synonym) == name.strip().lower())
    ).scalars().first()


# Deterministic identity (mirrors the validated POC): collapse footnote/symbol/paren variants and
# union by shared development code; curated clusters merge cross-name lineages (liso-cel -> BREYANZI).
_DEVCODE = re.compile(r"\b(?:BMS|CC|JNJ|ONO|CD)[-\s]?\d{3,}\b", re.I)
_SYM = re.compile(r"[®™✦^#*∗◆†‡§~]+")
_PARENS_RE = re.compile(r"\([^)]*\)")
_WS_RE = re.compile(r"\s+")


def _core(name: str) -> str:
    base = _SYM.sub("", name)
    base = _PARENS_RE.sub(" ", base)
    return _WS_RE.sub("", re.sub(r"[^a-z0-9 ]", " ", base.lower())).strip()


def _dev_codes(*names: str) -> set[str]:
    out = set()
    for n in names:
        out |= {re.sub(r"[-\s]", "", m).upper() for m in _DEVCODE.findall(n or "")}
    return out


def _seed_identity(s: Session, files: list[str], clusters: list[dict]) -> int:
    """Compute the full name->canonical grouping and seed asset_alias so every variant of a compound
    resolves to one asset_id during the rebuild (matching the POC's 269->150 identity)."""
    # union-find over cleaned cores
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    core_names: dict[str, set[str]] = {}   # core -> raw names seen
    code_core: dict[str, str] = {}
    for f in files:
        for a in json.load(open(f))["result"]["assets"]:
            names = [a["preferred_name"], *a.get("synonyms", [])]
            core = _core(a["preferred_name"])
            find(core)
            core_names.setdefault(core, set()).add(a["preferred_name"])
            for code in _dev_codes(*names):
                if code in code_core:
                    union(core, code_core[code])
                else:
                    code_core[code] = core
    for c in clusters:                      # curated cross-name merges
        cores = [_core(m) for m in [c["canonical"], *c["members"]]]
        for other in cores[1:]:
            union(cores[0], other)
        for m in [c["canonical"], *c["members"]]:
            core_names.setdefault(_core(m), set()).add(m)

    # group raw names by their connected component, pick one asset_id per group, alias all names to it
    groups: dict[str, set[str]] = {}
    for core, names in core_names.items():
        groups.setdefault(find(core), set()).update(names)

    # clean reseed of auto-generated aliases (human-curated decisions, if any, are preserved)
    s.execute(delete(AssetAlias).where(AssetAlias.method.in_(("curated", "deterministic"))))
    existing = {a.lower() for a in s.execute(select(AssetAlias.alias)).scalars()}
    added = 0
    for names in groups.values():
        canonical_aid = next((aid for n in names if (aid := _asset_id_for_name(s, n))), None)
        if not canonical_aid:
            continue
        for n in names:
            if n.lower() in existing:
                continue
            s.add(AssetAlias(asset_id=canonical_aid, alias=n, method="deterministic", confidence=0.9))
            existing.add(n.lower())
            added += 1
    s.flush()
    return added


def load_wayback_history(s: Session, company_name: str, extractions_dir: str,
                         aliases_path: str | None = None, url: str = BMS_URL) -> dict:
    company_id = _company_id(s, company_name)
    source_id = _source_id(s, company_id, url)

    files = sorted(glob.glob(f"{extractions_dir}/*.json"),
                   key=lambda f: json.load(open(f))["captured"])
    snaps_new = loaded = 0
    for f in files:
        d = json.load(open(f))
        captured = datetime.fromisoformat(d["captured"]).replace(tzinfo=UTC)
        result = ExtractionResult.model_validate(d["result"])

        snap_id = s.execute(
            select(Snapshot.snapshot_id).where(
                Snapshot.source_id == source_id, Snapshot.origin == "wayback",
                func.date(Snapshot.captured_at) == captured.date(),
            )
        ).scalar_one_or_none()
        if snap_id is None:
            content_hash = hashlib.sha256(json.dumps(d["result"], sort_keys=True).encode()).hexdigest()
            snap = Snapshot(
                source_id=source_id, captured_at=captured, origin="wayback",
                content_hash=content_hash, extraction_quality_score=_quality_score(result),
                render_meta={"quarter": d["quarter"], "captured": d["captured"],
                             "archive_url": f"https://web.archive.org/web/{d['ts']}/{url}"},
            )
            s.add(snap)
            s.flush()
            ext = Extraction(snapshot_id=snap.snapshot_id, model="claude-opus-4-8",
                             status="ok", raw_json=d["result"])
            s.add(ext)
            s.flush()
            snap_id = snap.snapshot_id
            ext_id = ext.extraction_id
            snaps_new += 1
        else:
            ext_id = s.execute(
                select(Extraction.extraction_id).where(Extraction.snapshot_id == snap_id)
                .order_by(Extraction.extracted_at.desc()).limit(1)
            ).scalar_one_or_none()
        if ext_id:
            load_extraction(s, ext_id)
            loaded += 1

    clusters = json.load(open(aliases_path)) if aliases_path else []
    aliases_added = _seed_identity(s, files, clusters)

    rebuilt = rebuild_history(s, company_id, origins=("wayback",))
    s.commit()
    return {
        "company_id": company_id, "source_id": source_id,
        "files": len(files), "snapshots_new": snaps_new, "extractions_loaded": loaded,
        "aliases_seeded": aliases_added,
        "total_assets": s.execute(select(func.count()).select_from(Asset)).scalar(),
        "change_events": s.execute(
            select(func.count()).select_from(ChangeEvent).where(ChangeEvent.company_id == company_id)
        ).scalar(),
        "rebuild": rebuilt,
    }
