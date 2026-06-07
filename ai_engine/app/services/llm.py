"""LLM provider resolution.

Builds an OpenHands ``LLM`` instance for the requested provider or model.

Routing
=======
- Anthropic models (``anthropic/...``) authenticate with an API key via
  ``ANTHROPIC_API_KEY`` (or a per-request override).
- Google Gemini models go through **Vertex AI** (``vertex_ai/...``) and
  authenticate with Application Default Credentials (ADC). No API key.
  AI Studio (``gemini/...`` + ``GOOGLE_API_KEY``) is no longer supported.

Key lookup order (Anthropic only)
=================================
1. ``user_api_key`` passed in the request.
2. Provider-specific env var (``ANTHROPIC_API_KEY``).
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
    - A full LiteLLM model string: ``"vertex_ai/gemini-3-flash-preview"``
      or ``"anthropic/claude-3-5-sonnet-20241022"``.
    - A bare provider key: ``"google"`` or ``"anthropic"``.

    Raises:
        ProviderError: if the model/provider is not supported.
        APIKeyMissingError: only for Anthropic (Vertex uses ADC, not keys).
    """
    # Map legacy / deprecated model IDs to their active equivalents. The
    # LEFT side is what legacy callers may still pass; the RIGHT side is
    # what we route to today. Old `gemini/...` ids predate the Vertex
    # migration; rewrite them so any caller that still passes the AI
    # Studio prefix transparently lands on Vertex routing.
    legacy_mappings = {
        "gemini/gemini-2.5-flash-preview":  "vertex_ai/gemini-3.5-flash",
        "gemini/gemini-2.5-pro-preview":    "vertex_ai/gemini-3.1-pro-preview",
        "gemini/gemini-3-flash-preview":    "vertex_ai/gemini-3.5-flash",
        "gemini/gemini-3.1-pro-preview":    "vertex_ai/gemini-3.1-pro-preview",
        "gemini/gemini-3.5-flash":          "vertex_ai/gemini-3.5-flash",
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

    # ── Vertex (Google) — no API key, ADC handles auth ───────
    if provider == "google":
        if not settings.GOOGLE_CLOUD_PROJECT:
            raise APIKeyMissingError(
                "GOOGLE_CLOUD_PROJECT is empty — Vertex auth requires a project. "
                "Set it in .env / docker-compose.yml."
            )
        # LiteLLM reads vertex_project + vertex_location from kwargs OR env.
        # Setting both makes the call succeed regardless of how LiteLLM
        # decided to look this up at any given version.
        os.environ.setdefault("VERTEXAI_PROJECT",  settings.GOOGLE_CLOUD_PROJECT)
        os.environ.setdefault("VERTEXAI_LOCATION", settings.GOOGLE_CLOUD_LOCATION or "global")
        llm_kwargs: dict = {
            "model":            model_id,
            "vertex_project":   settings.GOOGLE_CLOUD_PROJECT,
            "vertex_location":  settings.GOOGLE_CLOUD_LOCATION or "global",
            "safety_settings":  GEMINI_SAFETY_SETTINGS,
        }
        if settings.LLM_BASE_URL:
            llm_kwargs["base_url"] = settings.LLM_BASE_URL
        logger.info(
            "resolve_llm [%s] -> Vertex AI (project=%s, location=%s, ADC)",
            model_id,
            settings.GOOGLE_CLOUD_PROJECT,
            settings.GOOGLE_CLOUD_LOCATION or "global",
        )
        return LLM(**llm_kwargs)

    # ── Anthropic — API key path ──────────────────────────────
    resolved_key = user_api_key or os.getenv(config["env_key"], "")
    if not resolved_key:
        resolved_key = settings.LLM_API_KEY  # generic fallback

    if resolved_key:
        resolved_key = resolved_key.strip()
        if config.get("env_key"):
            os.environ[config["env_key"]] = resolved_key

    if not resolved_key:
        raise APIKeyMissingError(
            f"No API key found for {config['label']}. "
            f"Set the {config['env_key']} environment variable, "
            f"or provide a key in the request / user settings."
        )

    llm_kwargs = {
        "model":   model_id,
        "api_key": SecretStr(resolved_key),
    }
    if settings.LLM_BASE_URL:
        llm_kwargs["base_url"] = settings.LLM_BASE_URL

    logger.info(
        "resolve_llm [%s] -> Anthropic (key starts %s, len=%d)",
        model_id, resolved_key[:8], len(resolved_key),
    )
    return LLM(**llm_kwargs)
