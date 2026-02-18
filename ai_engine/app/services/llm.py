"""LLM provider resolution.

Builds an OpenHands ``LLM`` instance for the requested provider,
resolving the API key from (1) the user request, (2) the
provider-specific env var, or (3) the generic ``LLM_API_KEY`` fallback.
"""

from __future__ import annotations

import os

from pydantic import SecretStr

from app.config import (
    logger,
    settings,
    MODEL_CONFIGS,
    GEMINI_SAFETY_SETTINGS,
)
from app.sdk import LLM
from app.exceptions import ProviderError, APIKeyMissingError


def resolve_llm(provider: str, user_api_key: str | None = None):
    """Return a configured ``LLM`` instance for *provider*.

    Raises:
        ProviderError: if *provider* is not in ``MODEL_CONFIGS``.
        APIKeyMissingError: if no key can be found.
    """
    if provider not in MODEL_CONFIGS:
        raise ProviderError(
            f"Unsupported model provider: '{provider}'. "
            f"Choose from: {', '.join(MODEL_CONFIGS.keys())}"
        )

    config = MODEL_CONFIGS[provider]
    model_name = config["model"]

    resolved_key = (
        user_api_key
        or os.getenv(config["env_key"], "")
        or settings.LLM_API_KEY
    )

    if not resolved_key:
        raise APIKeyMissingError(
            f"No API key found for {config['label']}. "
            f"Set {config['env_key']} environment variable or "
            f"provide a key in the request."
        )

    llm_kwargs: dict = {
        "model": model_name,
        "api_key": SecretStr(resolved_key),
    }

    if settings.LLM_BASE_URL:
        llm_kwargs["base_url"] = settings.LLM_BASE_URL

    if provider == "google":
        llm_kwargs["safety_settings"] = GEMINI_SAFETY_SETTINGS
        logger.info("Using Gemini (%s) with safety_settings=BLOCK_NONE", model_name)
    else:
        logger.info("Using Anthropic (%s)", model_name)

    return LLM(**llm_kwargs)
