"""LLM provider resolution.

Builds an OpenHands ``LLM`` instance for the requested provider or model.

Key lookup order
================
1. A fully-qualified LiteLLM model identifier is looked up directly in
   ``MODEL_CONFIGS`` (e.g. ``"gemini/gemini-3-flash-preview"``).
2. If *model* is a bare provider name (``"google"`` / ``"anthropic"``),
   we fall back to the ``DEFAULT_MODEL_PER_PROVIDER`` value, which keeps
   the old call-sites working.

API key resolution order
========================
1. ``user_api_key`` passed in the request.
2. Provider-specific env var (e.g. ``GOOGLE_API_KEY``).
3. Generic ``LLM_API_KEY`` fallback.
"""

from __future__ import annotations

import os

from pydantic import SecretStr

from app.config import (
    logger,
    settings,
    MODEL_CONFIGS,
    DEFAULT_MODEL_PER_PROVIDER,
    GEMINI_SAFETY_SETTINGS,
)
from app.sdk import LLM
from app.exceptions import ProviderError, APIKeyMissingError


def resolve_llm(model_or_provider: str, user_api_key: str | None = None):
    """Return a configured ``LLM`` instance.

    *model_or_provider* can be:
    - A full LiteLLM model string: ``"gemini/gemini-3-flash-preview"``
    - A bare provider key: ``"google"`` or ``"anthropic"``

    Raises:
        ProviderError: if the model/provider is not supported.
        APIKeyMissingError: if no key can be found.
    """
    # Map legacy or deprecated model IDs to their new active equivalents
    legacy_mappings = {
        "gemini/gemini-2.5-flash-preview": "gemini/gemini-3-flash-preview",
        "gemini/gemini-2.5-pro-preview": "gemini/gemini-3.1-pro-preview",
    }
    model_or_provider = legacy_mappings.get(model_or_provider, model_or_provider)

    # ── resolve model string ──────────────────────────────────
    if model_or_provider in MODEL_CONFIGS:
        model_id = model_or_provider
    elif model_or_provider in DEFAULT_MODEL_PER_PROVIDER:
        # bare provider name — use default model for that provider
        model_id = DEFAULT_MODEL_PER_PROVIDER[model_or_provider]
    else:
        raise ProviderError(
            f"Unsupported model or provider: '{model_or_provider}'. "
            f"Supported models: {', '.join(MODEL_CONFIGS.keys())}"
        )

    config = MODEL_CONFIGS[model_id]
    provider = config["provider"]

    # ── resolve API key ───────────────────────────────────────
    resolved_key = user_api_key or os.getenv(config["env_key"], "")
    
    # Only fallback to generic LLM_API_KEY if we are actually using Google Gemini,
    # because our LLM_API_KEY in .env is specifically a Gemini key.
    if not resolved_key and provider == "google":
        resolved_key = settings.LLM_API_KEY

    if resolved_key:
        resolved_key = resolved_key.strip()
        # Force it into os.environ to bypass any LiteLLM bugs where it ignores kwargs
        if config.get("env_key"):
            os.environ[config["env_key"]] = resolved_key

    if not resolved_key:
        raise APIKeyMissingError(
            f"No API key found for {config['label']}. "
            f"Set the {config['env_key']} environment variable, "
            f"or provide a key in the request / user settings."
        )

    llm_kwargs: dict = {
        "model":   model_id,
        "api_key": SecretStr(resolved_key),
    }

    logger.info("resolve_llm [%s] -> Using API Key starting with: '%s' (len: %d)", model_id, resolved_key[:8] if resolved_key else "None", len(resolved_key) if resolved_key else 0)

    if settings.LLM_BASE_URL:
        llm_kwargs["base_url"] = settings.LLM_BASE_URL

    if provider == "google":
        llm_kwargs["safety_settings"] = GEMINI_SAFETY_SETTINGS
        logger.info("Using Google Gemini (%s) with safety_settings=BLOCK_NONE", model_id)
    else:
        logger.info("Using Anthropic (%s)", model_id)

    return LLM(**llm_kwargs)
