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
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str | None = None

    # ── Vertex AI (Google Cloud) ─────────────────────────────
    # All Gemini traffic — REST calls and LiteLLM-routed agent calls —
    # goes through Vertex AI using ADC (gcloud login locally, or service
    # account JSON / Workload Identity in prod). AI Studio
    # (generativelanguage.googleapis.com + GOOGLE_API_KEY) is NOT supported.
    GOOGLE_CLOUD_PROJECT: str = ""
    GOOGLE_CLOUD_LOCATION: str = "global"

    # ── Image services ───────────────────────────────────────
    UNSPLASH_ACCESS_KEY: str = ""

    # DEFAULT_MODEL_PROVIDER env var maps to DEFAULT_PROVIDER attribute
    DEFAULT_PROVIDER: str = Field("google", validation_alias="DEFAULT_MODEL_PROVIDER")

    # ── Classifier feature flag ──────────────────────────────
    # When True, the WS prompt-entry gate uses the new
    # ``project_classifier_agent.resolve_classification`` flow instead of
    # the legacy ``clarity_agent.check_prompt_clarity`` heuristic. The
    # new flow asks the same clarification questions (same WS event,
    # ``clarification_needed``) and additionally resolves the prompt to
    # a concrete archetype + entity list, injecting a
    # ``[LUCID_FORCE_ARCHETYPE::...]`` marker so the downstream
    # classifier short-circuits. Default OFF for safe rollout.
    USE_CLASSIFIER_AGENT: bool = False

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

    # Public-facing app root used to build the accept-invite link emailed
    # to invitees. Should be the URL the user types in their browser
    # (e.g. https://app.lucid.ai) — NOT the ai_engine API URL.
    # Falls back to the first ALLOWED_ORIGINS entry when unset.
    APP_URL: str = ""

    @property
    def app_url(self) -> str:
        """The public URL invitees click to land on /accept-invite."""
        if self.APP_URL:
            return self.APP_URL.rstrip("/")
        origins = self.allowed_origins_list
        return origins[0].rstrip("/") if origins else "http://localhost:3000"

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

    # ── Supabase Management API (per-project provisioning) ───
    # Personal access token from supabase.com/dashboard/account/tokens
    # with projects:write scope. Used to create per-customer Supabase
    # projects so each generated app has isolated data/keys.
    SUPABASE_MGMT_TOKEN: str = ""
    # Org under which new projects are created (a single Pro org holds
    # many projects; ~$25/mo + per-project compute). Find at
    # supabase.com/dashboard/org/<slug> → URL slug.
    SUPABASE_MGMT_ORG_REF: str = ""
    # Default region for newly provisioned projects. See
    # supabase.com/docs/guides/platform/regions for valid values.
    SUPABASE_DEFAULT_REGION: str = "us-east-1"
    # Random DB password length for new projects. The password is
    # generated server-side, encrypted, and stored in gen_project — never
    # shown to the user. Customer access is via the project-scoped JWTs.
    SUPABASE_DB_PASSWORD_LENGTH: int = 32

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

    @field_validator("INTERNAL_API_KEY")
    @classmethod
    def internal_api_key_required(cls, v: str) -> str:
        """Fail startup if INTERNAL_API_KEY is empty.

        When empty, the X-User-ID header is trusted without verification —
        any client could impersonate any user for server-to-server calls.
        Opt out for local dev by setting LUCID_ALLOW_NO_INTERNAL_KEY=1.
        """
        import os as _os
        if v:
            return v
        if _os.environ.get("LUCID_ALLOW_NO_INTERNAL_KEY") == "1":
            logger.warning(
                "INTERNAL_API_KEY is empty and LUCID_ALLOW_NO_INTERNAL_KEY=1 — "
                "X-User-ID will be accepted without verification (DEV ONLY)"
            )
            return v
        raise ValueError(
            "INTERNAL_API_KEY is required. Without it, any client sending "
            "X-User-ID can impersonate any user. Set INTERNAL_API_KEY to a "
            "shared secret matching frontend config, or set "
            "LUCID_ALLOW_NO_INTERNAL_KEY=1 to bypass for local dev only."
        )



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
    # ── Google Gemini (LiteLLM Vertex routing) ──────────────
    # The "vertex_ai/" prefix tells LiteLLM to authenticate via ADC and
    # hit the Vertex generateContent endpoint instead of AI Studio.
    # No API key is required; auth is project + location via ADC.
    "vertex_ai/gemini-3-flash-preview": {
        "provider": "google",
        "env_key":  "",  # no API key — Vertex uses ADC
        "label":    "Gemini 3 Flash Preview (Vertex)",
    },
    "vertex_ai/gemini-3.1-pro-preview": {
        "provider": "google",
        "env_key":  "",
        "label":    "Gemini 3 Pro Preview (Vertex)",
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
    "google":    "vertex_ai/gemini-3-flash-preview",
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
