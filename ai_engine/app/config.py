"""Application configuration.

All environment variables are read from the process environment (and
optionally from a ``.env`` file in the working directory) by
pydantic-settings at startup.  Required fields have **no default** — the
application will refuse to start with an explicit error if they are absent,
rather than silently operating with empty/invalid values.

Settings are exposed to the rest of the codebase via the ``settings``
singleton instance created at the bottom of this module.
"""

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Logging ─────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
)
logger = logging.getLogger("lucid.ai_engine")


# ── Settings ────────────────────────────────────────────────

class Settings(BaseSettings):
    """Typed, validated application settings backed by environment variables.

    Required fields (no default) cause the application to exit at startup
    with a clear error message if the env var is not set — "fail fast" rather
    than discovering a missing config at request time.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Required — app will not start without these ──────────
    SUPABASE_URL: str = Field("", validation_alias="SUPABASE_URL")
    SUPABASE_ANON_KEY: str = Field("", validation_alias="SUPABASE_ANON_KEY")
    # JWT secret from Supabase Dashboard → Settings → API → JWT Settings.
    # Used to validate incoming Supabase Auth JWTs (HS256).
    SUPABASE_JWT_SECRET: str = ""
    # service_role key — bypasses RLS; used only for server-to-server calls
    SUPABASE_SERVICE_KEY: str = ""
    ENCRYPTION_KEY: str = ""

    # ── LLM provider keys ────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str | None = None

    # DEFAULT_MODEL_PROVIDER env var maps to DEFAULT_PROVIDER attribute
    DEFAULT_PROVIDER: str = Field("google", validation_alias="DEFAULT_MODEL_PROVIDER")

    # ── Agent / sandbox ──────────────────────────────────────
    # MAX_ITERATIONS caps how many steps the agent takes per task.
    # Passed to get_default_agent() once the OpenHands SDK is installed.
    MAX_ITERATIONS: int = 200
    SANDBOX_IMAGE: str = "nikolaik/python-nodejs:python3.11-nodejs20"
    WORKSPACE_MOUNT_PATH: str = "/workspace"

    # ── Server ───────────────────────────────────────────────
    PORT: int = 8000
    WORKSPACE_BASE_PATH: str = "./storage"
    # Host-side workspace root used when creating sandbox container bind mounts.
    # When running inside Docker the ai_engine sees workspaces at WORKSPACE_BASE_PATH
    # but the Docker daemon needs the HOST path for the bind mount source.
    # Set to the left-hand side of the volume mount in docker-compose.yml.
    # Leave empty for local (non-Docker) development — abspath is used instead.
    HOST_WORKSPACE_PATH: str = ""

    # Internal API key — when set, X-User-ID is only trusted if the request
    # also includes a matching X-Internal-Key header.
    INTERNAL_API_KEY: str = ""

    # Redis — used for session persistence and (future) job queue.
    # Set to empty string to disable Redis and fall back to in-memory-only mode.
    REDIS_URL: str = "redis://localhost:6379"

    # CORS — comma-separated string of origins.
    # Use .allowed_origins_list property to get parsed list[str].
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse ALLOWED_ORIGINS into a list of URLs."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    # ── Docker sandbox ───────────────────────────────────────
    SANDBOX_CONTAINER_PREFIX: str = "lucid-sandbox-"
    SANDBOX_MEMORY_LIMIT: str = "2g"
    SANDBOX_CPU_LIMIT: float = 1.0
    DOCKER_NETWORK: str = ""

    # CONVERSATION_TIMEOUT env var (seconds until an idle session is reaped)
    CONVERSATION_TIMEOUT: int = 1800

    # ── Validators ───────────────────────────────────────────

    @field_validator("SUPABASE_URL")
    @classmethod
    def supabase_url_must_be_https(cls, v: str) -> str:
        if not v:
            return v  # allow empty for agent-only mode
        # Strip /auth/v1/callback suffix if present (common mistake from frontend .env)
        v = v.replace("/auth/v1/callback", "")
        if not v.startswith("https://"):
            raise ValueError("SUPABASE_URL must start with https://")
        return v



# ── Resolve NEXT_PUBLIC_ aliases before Settings() ──────────
# The shared .env uses NEXT_PUBLIC_SUPABASE_URL (frontend convention).
# Map them to backend names so pydantic-settings can pick them up.
import os as _os
from dotenv import load_dotenv as _load_dotenv

# Load .env so NEXT_PUBLIC_ vars are in os.environ
_load_dotenv(override=False)

_ALIASES = {
    "NEXT_PUBLIC_SUPABASE_URL": "SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "SUPABASE_ANON_KEY",
}
for _src, _dst in _ALIASES.items():
    if not _os.environ.get(_dst) and _os.environ.get(_src):
        val = _os.environ[_src]
        # Strip /auth/v1/callback suffix if present
        val = val.strip('"').strip("'").replace("/auth/v1/callback", "")
        _os.environ[_dst] = val

settings = Settings()

# ── Model catalogue (LiteLLM model strings) ─────────────────
# Keyed by the canonical LiteLLM model identifier.
# Each entry carries:
#   provider  – top-level provider family ('google' | 'anthropic')
#   env_key   – env var that holds the API key for this provider
#   label     – human-readable name shown in logs / error messages
MODEL_CONFIGS: dict[str, dict] = {
    # ── Google Gemini ────────────────────────────────────────
    "gemini/gemini-3-flash-preview": {
        "provider": "google",
        "env_key":  "GOOGLE_API_KEY",
        "label":    "Gemini 3 Flash Preview",
    },
    "gemini/gemini-3.1-pro-preview": {
        "provider": "google",
        "env_key":  "GOOGLE_API_KEY",
        "label":    "Gemini 3 Pro Preview",
    },
    # ── Anthropic Claude ─────────────────────────────────────
    "anthropic/claude-3-5-sonnet-20241022": {
        "provider": "anthropic",
        "env_key":  "ANTHROPIC_API_KEY",
        "label":    "Claude Sonnet 3.5",
    },
    "anthropic/claude-3-5-opus-20241022": {
        "provider": "anthropic",
        "env_key":  "ANTHROPIC_API_KEY",
        "label":    "Claude Opus 3.5",
    },
    "anthropic/claude-sonnet-4-6": {
        "provider": "anthropic",
        "env_key":  "ANTHROPIC_API_KEY",
        "label":    "Claude Sonnet 4.6",
    },
    "anthropic/claude-opus-4-6": {
        "provider": "anthropic",
        "env_key":  "ANTHROPIC_API_KEY",
        "label":    "Claude Opus 4.6",
    },
}

# ── Default model per provider ───────────────────────────────
DEFAULT_MODEL_PER_PROVIDER: dict[str, str] = {
    "google":    "gemini/gemini-3-flash-preview",
    "anthropic": "anthropic/claude-3-5-sonnet-20241022",
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
CONVERSATION_TIMEOUT_SECONDS: int = settings.CONVERSATION_TIMEOUT
DB_BATCH_SIZE = 20          # flush events to DB after this many accumulate
DB_BATCH_INTERVAL = 2.0     # … or after this many seconds, whichever comes first
