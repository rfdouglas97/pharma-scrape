"""Bronze layer: persist a rendered page as an immutable snapshot + artifacts.

Implements the hash-skip: if the rendered content hash matches the most recent
snapshot for this source, we still record a (lightweight, artifact-less) snapshot row
marked `unchanged` and skip re-extraction. This is what keeps weekly cadence cheap.
"""

from __future__ import annotations

import hashlib
import mimetypes
import urllib.request
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline_intel.gold.models import Snapshot
from pipeline_intel.ingest.hashing import content_hash
from pipeline_intel.ingest.render import RenderResult
from pipeline_intel.ingest.storage import Storage


def _artifact_prefix(company_slug: str, source_id: str, h: str) -> str:
    return f"{company_slug}/{date.today().isoformat()}/{source_id[:8]}_{h[:12]}"


MAX_LINKED_IMAGE_BYTES = 8_000_000


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
    image_payloads, image_errors = _fetch_pipeline_images(result.meta)
    h = content_hash(_hash_material(result.text, result.meta, image_payloads, image_errors))
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
    image_keys = _save_pipeline_images(storage, prefix, image_payloads)

    snap = Snapshot(
        source_id=source_id,
        http_status=result.http_status,
        content_hash=h,
        html_key=html_key,
        screenshot_keys=[shot_key],
        pdf_keys=[],
        render_meta={
            **result.meta,
            "text_key": text_key,
            "pipeline_image_keys": image_keys,
            "pipeline_image_errors": image_errors,
        },
        unchanged=False,
    )
    s.add(snap)
    s.flush()
    return snap, True


def _fetch_pipeline_images(meta: dict) -> tuple[list[dict], list[dict]]:
    """Fetch linked image artifacts that look like pipeline evidence.

    Failures are recorded in metadata instead of failing the snapshot; the full-page
    screenshot remains the fallback visual artifact.
    """
    payloads: list[dict] = []
    errors: list[dict] = []
    for url in meta.get("pipeline_image_urls") or []:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PipelineIntelBot/0.1"})
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - URL came from rendered page
                content_type = resp.headers.get_content_type() or _guess_content_type(url)
                data = resp.read(MAX_LINKED_IMAGE_BYTES + 1)
            if len(data) > MAX_LINKED_IMAGE_BYTES:
                raise ValueError(f"image exceeds {MAX_LINKED_IMAGE_BYTES} byte cap")
            payloads.append({"url": url, "content_type": content_type, "data": data})
        except Exception as exc:  # noqa: BLE001 - capture artifact failures for QA/repair
            errors.append({"url": url, "error": str(exc)})
    return payloads, errors


def _save_pipeline_images(storage: Storage, prefix: str, payloads: list[dict]) -> list[str]:
    keys: list[str] = []
    for idx, payload in enumerate(payloads, start=1):
        content_type = payload["content_type"]
        ext = mimetypes.guess_extension(content_type) or ".img"
        key = storage.put(f"{prefix}/linked-image-{idx}{ext}", payload["data"], content_type)
        keys.append(key)
    return keys


def _hash_material(text: str, meta: dict, image_payloads: list[dict], image_errors: list[dict]) -> str:
    parts = [text]
    for payload in image_payloads:
        digest = hashlib.sha256(payload["data"]).hexdigest()
        parts.append(f"pipeline_image:{payload['url']}:{digest}")
    for error in image_errors:
        parts.append(f"pipeline_image_error:{error['url']}:{error['error']}")
    if not image_payloads and not image_errors:
        parts.extend(f"pipeline_image_url:{url}" for url in meta.get("pipeline_image_urls") or [])
    return "\n".join(parts)


def _guess_content_type(url: str) -> str:
    return mimetypes.guess_type(url.split("?", 1)[0])[0] or "application/octet-stream"
