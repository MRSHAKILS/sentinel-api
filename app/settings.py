"""Runtime settings read from environment variables.

Read lazily (per call) so tests can flip env vars and so a deployment platform's
env injection is always respected. The LLM is treated as fully optional: if it is
disabled or no key is present, the service runs as a pure deterministic rule
engine with identical behaviour to the LLM-free build.
"""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def use_llm() -> bool:
    return os.getenv("USE_LLM", "false").strip().lower() in _TRUTHY


def api_key() -> str:
    return (os.getenv("OPENROUTER_API_KEY", "") or "").strip()


def llm_enabled() -> bool:
    """The LLM assist path runs only when explicitly enabled AND a key exists."""
    return use_llm() and bool(api_key())


def model() -> str:
    return os.getenv("LLM_MODEL", "google/gemini-2.5-flash").strip()


def base_url() -> str:
    return os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")


def timeout_seconds() -> float:
    try:
        return float(os.getenv("LLM_TIMEOUT_SECONDS", "4.5"))
    except (TypeError, ValueError):
        return 4.5
