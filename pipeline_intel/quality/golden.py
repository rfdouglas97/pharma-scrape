"""Scaffold a golden-set fixture from a real captured snapshot, so labeling a page is
just filling in expected.json (the page text + screenshot are copied from bronze).
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from pipeline_intel.db import session
from pipeline_intel.gold.models import Company, CompanySource, Snapshot
from pipeline_intel.ingest.storage import get_storage
from pipeline_intel.quality.eval_harness import GOLDEN_DIR

_TEMPLATE = {
    "assets": [
        {
            "preferred_name": "FILL ME",
            "synonyms": [],
            "modality_verbatim": None,
            "target_verbatim": None,
            "mechanism_verbatim": None,
            "originator_verbatim": None,
            "partners": [],
            "programs": [
                {"indication_verbatim": "FILL ME", "phase_verbatim": "FILL ME",
                 "status": None, "additional_fields": []}
            ],
            "additional_fields": [],
        }
    ],
    "page_notes": None,
}


def scaffold_from_snapshot(snapshot_id: str, fmt: str = "unknown", golden_dir: Path = GOLDEN_DIR) -> dict:
    storage = get_storage()
    with session() as s:
        snap = s.get(Snapshot, snapshot_id)
        if snap is None:
            return {"error": f"snapshot {snapshot_id} not found"}
        if not snap.html_key:
            return {"error": "snapshot has no artifacts (unchanged snapshot) — re-run ingest"}
        source = s.get(CompanySource, snap.source_id)
        company = s.get(Company, source.company_id) if source else None
        slug = _slug(company.name if company else snap.snapshot_id)

        out = golden_dir / slug
        out.mkdir(parents=True, exist_ok=True)

        text_key = (snap.render_meta or {}).get("text_key")
        if text_key:
            (out / "page.txt").write_bytes(storage.get(text_key))
        for k in snap.screenshot_keys or []:
            (out / "page.png").write_bytes(storage.get(k))
            break  # first screenshot

        (out / "meta.json").write_text(json.dumps(
            {"company": company.name if company else "", "url": source.url if source else "",
             "format": fmt, "snapshot_id": snapshot_id}, indent=2))
        expected = out / "expected.json"
        if not expected.exists():  # never clobber human labels
            expected.write_text(json.dumps(_TEMPLATE, indent=2))

        return {"slug": slug, "dir": str(out), "label_file": str(expected),
                "next": "Fill in expected.json with the hand-labeled pipeline, then run `pipeline eval`."}


def latest_snapshot_for_company(company_query: str) -> str | None:
    with session() as s:
        company = s.execute(
            select(Company).where(Company.name.ilike(f"%{company_query}%")).limit(1)
        ).scalar_one_or_none()
        if company is None:
            return None
        return s.execute(
            select(Snapshot.snapshot_id)
            .join(CompanySource, CompanySource.source_id == Snapshot.source_id)
            .where(CompanySource.company_id == company.company_id, Snapshot.html_key.isnot(None))
            .order_by(Snapshot.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()


def _slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
