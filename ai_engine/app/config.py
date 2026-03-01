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
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    # JWT secret from Supabase Dashboard → Settings → API → JWT Settings.
    # Used to validate incoming Supabase Auth JWTs (HS256).
    SUPABASE_JWT_SECRET: str
    # service_role key — bypasses RLS; used only for server-to-server calls
    # (X-Internal-Key path) where no user JWT is available.  Never expose to clients.
    SUPABASE_SERVICE_KEY: str
    ENCRYPTION_KEY: str

    # ── LLM provider keys ────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str | None = None

    # DEFAULT_MODEL_PROVIDER env var maps to DEFAULT_PROVIDER attribute
    DEFAULT_PROVIDER: str = Field("anthropic", validation_alias="DEFAULT_MODEL_PROVIDER")

    # ── Agent / sandbox ──────────────────────────────────────
    # MAX_ITERATIONS caps how many steps the agent takes per task.
    # Passed to get_default_agent() once the OpenHands SDK is installed.
    MAX_ITERATIONS: int = 50
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

    # CORS — multiple origins as a comma-separated string.
    # Parsed into a list by the validator below so callers get list[str].
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

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
        if not v.startswith("https://"):
            raise ValueError("SUPABASE_URL must start with https://")
        return v

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: object) -> list[str]:
        if isinstance(v, list):
            return v
        return [o.strip() for o in str(v).split(",") if o.strip()]


settings = Settings()

# Provider-specific model configs (LiteLLM naming convention)
MODEL_CONFIGS: dict[str, dict] = {
    "google": {
        "model": "gemini-3-flash-preview",
        "env_key": "GOOGLE_API_KEY",
        "label": "Gemini 2.5 Flash",
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
CONVERSATION_TIMEOUT_SECONDS: int = settings.CONVERSATION_TIMEOUT
DB_BATCH_SIZE = 20          # flush events to DB after this many accumulate
DB_BATCH_INTERVAL = 2.0     # … or after this many seconds, whichever comes first
