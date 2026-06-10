"""Artifact storage abstraction. Local filesystem for dev; S3-compatible (Supabase
Storage / Cloudflare R2) in prod — same key scheme so nothing downstream changes.

Keys are content-addressed by company/date/hash so re-runs are stable and the raw
artifact for any gold value is always retrievable (provenance requirement).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pipeline_intel.config import settings


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> str: ...
    def get(self, key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


class S3Storage:
    """S3-compatible backend (Supabase Storage / R2). Lazy boto3 import so dev installs
    don't need it."""

    def __init__(self) -> None:
        import boto3  # noqa: PLC0415 — optional dependency, only needed in prod

        s = settings()
        self._bucket = s.artifact_s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=s.artifact_s3_endpoint,
            aws_access_key_id=s.artifact_s3_access_key,
            aws_secret_access_key=s.artifact_s3_secret_key,
        )

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()


def get_storage() -> Storage:
    s = settings()
    if s.artifact_backend == "s3":
        return S3Storage()
    return LocalStorage(s.artifact_local_dir)
