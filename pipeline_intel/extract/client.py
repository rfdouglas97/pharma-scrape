"""Anthropic client factory. Centralized so model id and key handling live in one place."""

from __future__ import annotations

from functools import lru_cache

from pipeline_intel.config import settings

HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-8"

# Cost-aware production default: Sonnet handles normal extraction; Opus is reserved for
# routing/escalation when the QA gate or source complexity says frontier reasoning matters.
DEFAULT_EXTRACTION_MODEL = SONNET_MODEL


@lru_cache
def get_client():
    import anthropic  # noqa: PLC0415 — optional at import time; only needed when extracting

    key = settings().anthropic_api_key
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env to run extraction."
        )
    return anthropic.Anthropic(api_key=key)
