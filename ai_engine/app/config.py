"""
Application configuration.

All environment variables are read once at import time and exposed
as module-level constants grouped by concern.  For structured access
use the ``settings`` instance.
"""

import os
import logging

from dotenv import load_dotenv

load_dotenv()

# ── Logging ─────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
)
logger = logging.getLogger("lucid.ai_engine")


# ── Settings ────────────────────────────────────────────────

class Settings:
    """Typed, validated application settings."""

    # LLM provider keys
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str | None = os.getenv("LLM_BASE_URL")

    DEFAULT_PROVIDER: str = os.getenv("DEFAULT_MODEL_PROVIDER", "anthropic")

    # Agent / sandbox
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "50"))
    SANDBOX_IMAGE: str = os.getenv(
        "SANDBOX_IMAGE", "nikolaik/python-nodejs:python3.11-nodejs20"
    )
    WORKSPACE_MOUNT_PATH: str = os.getenv("WORKSPACE_MOUNT_PATH", "/workspace")

    # Server
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "change_me_in_prod")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://lucid_user:lucid_password@localhost:5432/lucid_db",
    )

    # Workspace isolation
    WORKSPACE_BASE_PATH: str = os.getenv("WORKSPACE_BASE_PATH", "./storage")

    # Internal API key — when set, X-User-ID header is only trusted
    # if the request also includes a matching X-Internal-Key header.
    # This prevents external callers from impersonating users.
    INTERNAL_API_KEY: str = os.getenv("INTERNAL_API_KEY", "")

    # CORS — restrict which origins may call the REST API.
    # In production this should be set to the frontend URL only.
    # Multiple origins can be specified as a comma-separated list.
    ALLOWED_ORIGINS: list = [
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]

    # Docker sandbox (per-session containers)
    SANDBOX_CONTAINER_PREFIX: str = os.getenv("SANDBOX_CONTAINER_PREFIX", "lucid-sandbox-")
    SANDBOX_MEMORY_LIMIT: str = os.getenv("SANDBOX_MEMORY_LIMIT", "2g")
    SANDBOX_CPU_LIMIT: float = float(os.getenv("SANDBOX_CPU_LIMIT", "1.0"))
    DOCKER_NETWORK: str = os.getenv("DOCKER_NETWORK", "")

    # Encryption key for git tokens — must match frontend ENCRYPTION_KEY.
    # The raw value is SHA-256 hashed to produce a 32-byte AES-256 key.
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")

settings = Settings()

# Provider-specific model configs (LiteLLM naming convention)
MODEL_CONFIGS: dict[str, dict] = {
    "google": {
        "model": "gemini/gemini-2.0-flash",
        "env_key": "GOOGLE_API_KEY",
        "label": "Gemini 2.0 Flash",
    },
    "anthropic": {
        "model": "anthropic/claude-3-5-sonnet-20241022",
        "env_key": "ANTHROPIC_API_KEY",
        "label": "Claude 3.5 Sonnet",
    },
}

# Gemini safety settings — disable content filters for coding agents
GEMINI_SAFETY_SETTINGS: list[dict] = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


# ── Limits / magic numbers ──────────────────────────────────

WS_EVENT_MAX_CHARS = 2000
THOUGHT_MAX_CHARS = 1000
WS_INIT_TIMEOUT_SECONDS = 30.0
MOCK_STEP_DELAY_SECONDS = 1.5
EVENT_BUFFER_MAX_SIZE = 1000
CONVERSATION_TIMEOUT_SECONDS = int(os.getenv("CONVERSATION_TIMEOUT", "1800"))  # 30 min
DB_BATCH_SIZE = 20          # flush events to DB after this many accumulate
DB_BATCH_INTERVAL = 2.0     # … or after this many seconds, whichever comes first
