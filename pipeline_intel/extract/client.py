"""Anthropic client factory. Centralized so model id and key handling live in one place."""

from __future__ import annotations

from functools import lru_cache

from pipeline_intel.config import settings

# Production extraction model (per ENGINEERING_PLAN). The golden-set eval can compare
# this against cheaper tiers (Sonnet 4.6, Haiku 4.5) to pick the cheapest that holds the bar.
DEFAULT_EXTRACTION_MODEL = "claude-opus-4-8"


@lru_cache
def get_client():
    import anthropic  # noqa: PLC0415 — optional at import time; only needed when extracting

    key = settings().anthropic_api_key
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env to run extraction."
        )
    return anthropic.Anthropic(api_key=key)
