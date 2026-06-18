"""Download a pipeline document (PDF / XLSX / CSV) for the document-ingestion path.

Parallel to `render.py` for pages: where a CompanySource points at a downloadable file,
we fetch the bytes directly (httpx) rather than driving Playwright. Good-citizen identity
(crawler UA) and a size cap apply; robots is checked by the caller (`run.py`), as for pages.
"""

from __future__ import annotations

import mimetypes
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from pipeline_intel.config import settings

# Pipeline files are usually small, but quarterly spreadsheets / multi-page PDFs can be a
# few MB. Cap generously; anything larger is almost certainly the wrong link.
MAX_DOC_BYTES = 50_000_000
FETCH_TIMEOUT_SECONDS = 30.0


@dataclass
class DocFetch:
    url: str
    http_status: int | None
    content_type: str | None
    raw_bytes: bytes
    ext: str | None


class DocFetchError(RuntimeError):
    pass


def url_ext(url: str) -> str | None:
    """Lower-cased file extension from a URL path (query/fragment stripped), or None."""
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    return ext or None


def _read_capped(chunks: Iterable[bytes], cap: int = MAX_DOC_BYTES) -> bytes:
    """Concatenate chunks, raising DocFetchError once the cap is exceeded."""
    out: list[bytes] = []
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > cap:
            raise DocFetchError(f"document exceeds {cap} byte cap")
        out.append(chunk)
    return b"".join(out)


def _local_path(url: str) -> Path | None:
    """Resolve a URL to a local file path when it points at one (a `file://` URL or a plain
    relative/absolute path), else None. Lets a CompanySource point at a curated eval PDF."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path))
    elif parsed.scheme in ("", None):
        candidate = Path(unquote(url))
    else:
        return None
    return candidate if candidate.exists() else None


def fetch_document(url: str) -> DocFetch:
    local = _local_path(url)
    if local is not None:
        raw = _read_capped(iter([local.read_bytes()]))
        ctype = mimetypes.guess_type(local.name)[0]
        return DocFetch(url=url, http_status=200, content_type=ctype, raw_bytes=raw,
                        ext=local.suffix.lower() or None)

    import httpx  # noqa: PLC0415 — defer import

    headers = {"User-Agent": settings().crawler_user_agent}
    try:
        with httpx.stream(
            "GET", url, headers=headers, follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS
        ) as resp:
            status = resp.status_code
            content_type = resp.headers.get("content-type")
            if status >= 400:
                raise DocFetchError(f"HTTP {status} fetching {url}")
            raw = _read_capped(resp.iter_bytes())
    except DocFetchError:
        raise
    except httpx.HTTPError as exc:
        raise DocFetchError(str(exc)) from exc
    return DocFetch(url=url, http_status=status, content_type=content_type, raw_bytes=raw, ext=url_ext(url))
