"""Bronze layer: persist a rendered page as an immutable snapshot + artifacts.

Implements the hash-skip: if the rendered content hash matches the most recent
snapshot for this source, we still record a (lightweight, artifact-less) snapshot row
marked `unchanged` and skip re-extraction. This is what keeps weekly cadence cheap.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import Snapshot
from pipeline_intel.ingest.hashing import content_hash
from pipeline_intel.ingest.render import RenderResult
from pipeline_intel.ingest.storage import Storage


def _artifact_prefix(company_slug: str, source_id: str, h: str) -> str:
    return f"{company_slug}/{date.today().isoformat()}/{source_id[:8]}_{h[:12]}"


def latest_hash(s: Session, source_id: str) -> str | None:
    # ULID PK tiebreaks same-second fetched_at so the genuinely-latest insert always wins
    # (monotonic ULIDs); otherwise two snapshots written in the same second order arbitrarily.
    return s.execute(
        select(Snapshot.content_hash)
        .where(Snapshot.source_id == source_id)
        .order_by(Snapshot.fetched_at.desc(), Snapshot.snapshot_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def write_snapshot(
    s: Session,
    storage: Storage,
    source_id: str,
    company_slug: str,
    result: RenderResult,
) -> tuple[Snapshot, bool]:
    """Returns (snapshot, changed). When unchanged, no artifacts are stored."""
    h = content_hash(result.text)
    prev = latest_hash(s, source_id)
    changed = h != prev

    if not changed:
        snap = Snapshot(
            source_id=source_id,
            http_status=result.http_status,
            content_hash=h,
            render_meta=result.meta,
            unchanged=True,
        )
        s.add(snap)
        s.flush()
        return snap, False

    prefix = _artifact_prefix(company_slug, source_id, h)
    html_key = storage.put(f"{prefix}/page.html", result.html.encode("utf-8"), "text/html")
    text_key = storage.put(f"{prefix}/page.txt", result.text.encode("utf-8"), "text/plain")
    shot_key = storage.put(f"{prefix}/page.png", result.screenshot, "image/png")

    snap = Snapshot(
        source_id=source_id,
        http_status=result.http_status,
        content_hash=h,
        html_key=html_key,
        screenshot_keys=[shot_key],
        pdf_keys=[],
        render_meta={**result.meta, "text_key": text_key},
        unchanged=False,
    )
    s.add(snap)
    s.flush()
    return snap, True
