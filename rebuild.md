# Lucid AI — Rebuild From Scratch

Step-by-step order. Create each file before moving to the next phase.
No code is included here — read the existing repo for implementation details.
After every phase, write the listed tests and confirm they pass before continuing.

---

## Phase 1 — Supabase

1. Create a project at supabase.com. Collect from the Dashboard:
   - **Project URL** → `SUPABASE_URL` (Settings → API)
   - **Publishable key** (formerly `anon`) → `SUPABASE_ANON_KEY` (Settings → API)
   - **JWT Secret** → `SUPABASE_JWT_SECRET` (Settings → API → JWT Settings)
   - **Secret key** (formerly `service_role`) → `SUPABASE_SERVICE_KEY` (Settings → API)

3. Create OAuth apps and configure them in Supabase:

   For **every** provider the Supabase callback URL is:
   ```
   https://<your-project-ref>.supabase.co/auth/v1/callback
   ```

   **Google** — https://console.cloud.google.com → APIs & Services → Credentials
   → Create OAuth 2.0 Client ID → Application type: **Web application**
   → Add the callback URL above as an Authorized redirect URI.
   Copy **Client ID** → `GOOGLE_CLIENT_ID`, **Client Secret** → `GOOGLE_CLIENT_SECRET`.

   **GitHub** — https://github.com/settings/applications/new
   → Set **Authorization callback URL** to the callback URL above.
   Copy **Client ID** → `GITHUB_CLIENT_ID`, generate a **Client Secret** → `GITHUB_CLIENT_SECRET`.

   **GitLab** — https://gitlab.com/-/user_settings/applications (or your self-hosted instance)
   → Set **Redirect URI** to the callback URL above.
   → Scopes: `read_user`, `openid`, `email`.
   Copy **Application ID** → `GITLAB_CLIENT_ID`, **Secret** → `GITLAB_CLIENT_SECRET`.
   For self-hosted GitLab also set the instance URL in Supabase Dashboard →
   Authentication → Providers → GitLab → GitLab URL.

   In the Supabase Dashboard → Authentication → Providers: enable each provider and
   paste the client ID and secret.

   In Dashboard → Authentication → URL Configuration:
   - **Site URL**: your app's root URL (e.g. `https://yourdomain.com`)
   - **Additional redirect URLs**: any extra allowed redirect targets

4. Apply migrations via the Supabase Dashboard SQL editor:

   Dashboard → SQL Editor → **New query**. Run each file in order:
   - `supabase/migrations/001_initial_schema.sql`
   - `supabase/migrations/002_integrations.sql`

   This creates `users`, `chat_sessions`, `chat_messages` with indexes, triggers,
   and RLS policies. `users` is a public profile table that references `auth.users`
   — Supabase Auth manages the identity; a trigger auto-creates the profile row on
   first sign-in. The `integrations` table is created by
   `002_integrations.sql` along with its RLS policies.

   Adding new tables in future: create a new SQL file in `supabase/migrations/` and
   run it in the SQL editor.

### Verify Phase 1

In the Supabase Dashboard → Table Editor, confirm:
- Tables `users`, `chat_sessions`, `chat_messages` exist with the expected columns.
- Dashboard → Authentication → Policies: RLS is enabled on all three tables and
  each has SELECT / INSERT / UPDATE / DELETE policies.
- Dashboard → Database → Functions: `handle_new_user` and `set_updated_at` exist.
- Dashboard → Database → Triggers: `on_auth_user_created` is attached to `auth.users`.

Test the trigger manually:
1. Dashboard → Authentication → Users → **Invite user** (or use the Supabase Auth test flow).
2. Confirm a new row appears in `public.users` with matching `id`, `email`, and
   `name`/`avatar_url` populated from the OAuth metadata.

---

## Phase 2 — AI Engine: foundation

5. `ai_engine/requirements.txt`
   Python dependencies: fastapi, uvicorn, pydantic, pydantic-settings, websockets,
   python-jose, cryptography, supabase, google-generativeai, litellm, docker,
   python-dotenv, httpx.

6. `ai_engine/main.py`
   Uvicorn entrypoint — imports `app` from `app` package and runs it.

7. `ai_engine/Dockerfile`
   Python base image, copies requirements.txt, runs pip install, exposes port 8000.

8. `ai_engine/app/__init__.py`
   FastAPI app factory (`create_app`). Registers CORS middleware and all routers.
   Defines `lifespan` context manager: checks Docker availability on startup,
   cleans up sessions and containers on shutdown. Required Supabase env vars are
   validated at import time by Pydantic BaseSettings (no runtime warnings needed).

### Verify Phase 2

```bash
cd ai_engine
pip install -r requirements.txt
SUPABASE_URL=https://x.supabase.co \
SUPABASE_ANON_KEY=x \
SUPABASE_JWT_SECRET=x \
SUPABASE_SERVICE_KEY=x \
ENCRYPTION_KEY=x \
uvicorn main:app --port 8000
```

The server must start without import errors. Stop it before continuing.

---

## Phase 3 — AI Engine: config and core helpers

9. `ai_engine/app/config.py`
   `Settings(BaseSettings)` — pydantic-settings reads env vars and `.env` at startup.
   Required fields (app refuses to start if missing): `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
   `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_KEY`, `ENCRYPTION_KEY`.
   Optional with defaults: `INTERNAL_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
   `LLM_API_KEY`, `DEFAULT_MODEL_PROVIDER` (alias for `DEFAULT_PROVIDER`),
   `MAX_ITERATIONS`, sandbox settings, `ALLOWED_ORIGINS` (comma-separated → list).
   Validators: `SUPABASE_URL` must start with `https://`.
   Also defines `MODEL_CONFIGS`, `WS_EVENT_MAX_CHARS`, `DB_BATCH_SIZE`, etc.

10. `ai_engine/app/exceptions.py`
    Custom exception classes used across the app.

11. `ai_engine/app/schemas.py`
    Pydantic request/response models shared between routers.

12. `ai_engine/app/auth.py`
    JWT validation using `SUPABASE_JWT_SECRET` (HS256, audience check skipped).
    FastAPI dependency `get_current_user`, plus `authenticate_websocket` and
    `authenticate_from_handshake` for WebSocket auth.
    Auth order: Bearer JWT → X-User-ID + X-Internal-Key (constant-time compare).
    `AuthenticatedUser.raw_jwt` is the original Supabase Auth token (str) for the
    Bearer path, or `None` for the X-Internal-Key path (services use the admin
    client — service_role key — when `raw_jwt` is None).

13. `ai_engine/app/sdk.py`
    Try-import of OpenHands SDK packages. Exports `OPENHANDS_AVAILABLE` boolean
    and `import_error` string. The rest of the app checks this flag before using
    any SDK types — allows mock mode when SDK is not installed.

14. `ai_engine/app/supabase_client.py`
    Three async context managers — import `db_client` in services, not the others:
    - `managed_client(user_jwt)` — anon key + user JWT → RLS enforced. Fresh client
      per call to avoid JWT cross-contamination across concurrent requests.
    - `managed_admin_client()` — service_role key → RLS bypassed. Use only for
      server-to-server calls where no user JWT is available; callers must filter
      by `user_id` explicitly.
    - `db_client(user_jwt: str | None)` — dispatcher: yields `managed_client` when
      `user_jwt` is a string, `managed_admin_client` when it is `None`.

### Test setup (run once before Phase 3 tests)

Create `ai_engine/requirements-dev.txt`:
```
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
```

```bash
cd ai_engine
pip install -r requirements-dev.txt
```

Create `ai_engine/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
```

### Tests — Phase 3

**`ai_engine/tests/__init__.py`** — empty.

**`ai_engine/tests/conftest.py`** — shared fixtures used by all test files:

```python
import pytest
import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from jose import jwt
from app import app
from app.config import settings

TEST_USER_ID = "test-user-uuid-0001"
TEST_USER_EMAIL = "test@example.com"

@pytest.fixture
def auth_token():
    """Valid JWT for TEST_USER_ID — signed with SUPABASE_JWT_SECRET."""
    payload = {
        "sub": TEST_USER_ID,
        "email": TEST_USER_EMAIL,
        "role": "authenticated",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")

@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}

@pytest.fixture
def mock_supabase():
    """Returns a mock that mimics the async Supabase client chain."""
    client = MagicMock()
    for method in ("table", "select", "insert", "update", "upsert", "delete",
                   "eq", "order", "range", "maybe_single", "single", "limit"):
        getattr(client, method).return_value = client
    client.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.aclose = AsyncMock()
    return client

@pytest.fixture
def patch_supabase(mock_supabase):
    """Patch db_client() so services receive the mock Supabase client."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_db_client(user_jwt):
        yield mock_supabase

    with patch("app.services.chat.db_client", fake_db_client), \
         patch("app.services.integrations.db_client", fake_db_client):
        yield mock_supabase

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

**`ai_engine/tests/test_auth.py`**:

```python
import pytest

@pytest.mark.asyncio
async def test_no_token_returns_403(client):
    resp = await client.get("/api/v1/chats")
    assert resp.status_code == 403

@pytest.mark.asyncio
async def test_bad_token_returns_401(client):
    resp = await client.get("/api/v1/chats",
                            headers={"Authorization": "Bearer bad.token"})
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_valid_token_passes(client, auth_headers, patch_supabase):
    from unittest.mock import AsyncMock, MagicMock
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=[]))
    resp = await client.get("/api/v1/chats", headers=auth_headers)
    assert resp.status_code == 200
```

Run:
```bash
cd ai_engine && pytest tests/test_auth.py -v
```

All 3 tests must pass before continuing.

---

## Phase 4 — AI Engine: services

16. `ai_engine/app/services/__init__.py`
    Empty.

17. `ai_engine/app/services/chat.py`
    `ChatService` — stateless class. Every method accepts `user_jwt: str | None`
    and uses `db_client(user_jwt)` internally (RLS-enforced client when JWT is
    present, admin client when None). All methods wrap DB calls in APIError + Exception
    handlers that log and raise HTTP 500.
    Methods: `create_session`, `list_sessions`, `get_session`, `delete_session`,
    `rename_session`, `add_message`, `add_messages` (batch insert), `deactivate_session`.
    All methods return plain `dict` / `list[dict]` — no separate type file needed.

### Tests — `chat.py`

**`ai_engine/tests/test_chat_service.py`** — unit tests for `ChatService` in isolation:

```python
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

SESSION_ID = str(uuid.uuid4())
USER_ID = "user-abc"
TEST_JWT = "test.jwt.token"

def make_mock_client(data=None):
    client = MagicMock()
    for m in ("table", "select", "insert", "update", "delete", "upsert",
              "eq", "order", "range", "maybe_single"):
        getattr(client, m).return_value = client
    client.execute = AsyncMock(return_value=MagicMock(data=data or []))
    client.aclose = AsyncMock()
    return client

def mock_managed(mock_client):
    @asynccontextmanager
    async def _fake(user_jwt):
        yield mock_client
    return _fake

@pytest.mark.asyncio
async def test_create_session_returns_dict():
    mock = make_mock_client(data=[{
        "id": SESSION_ID, "user_id": USER_ID, "title": "T",
        "agent_session_id": "s1", "project_id": None,
        "model_provider": "anthropic", "is_active": True,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }])
    with patch("app.services.chat.db_client", mock_managed(mock)):
        from app.services.chat import ChatService
        result = await ChatService.create_session(
            user_id=USER_ID, user_jwt=TEST_JWT, agent_session_id="s1", title="T")
    assert result["id"] == SESSION_ID
    assert result["user_id"] == USER_ID

@pytest.mark.asyncio
async def test_add_messages_batch_insert():
    """add_messages must call insert once with a list, not N times."""
    mock = make_mock_client(data=[])
    with patch("app.services.chat.db_client", mock_managed(mock)):
        from app.services.chat import ChatService
        events = [
            {"content": "step 1", "eventType": "CmdRunAction"},
            {"content": "step 2", "eventType": "CmdOutputObservation"},
        ]
        await ChatService.add_messages(events, SESSION_ID, user_jwt=TEST_JWT)
    mock.table.assert_called_once_with("chat_messages")
    inserted = mock.insert.call_args[0][0]
    assert isinstance(inserted, list)
    assert len(inserted) == 2
    assert all(r["session_id"] == SESSION_ID for r in inserted)
    assert all(r["role"] == "assistant" for r in inserted)

@pytest.mark.asyncio
async def test_add_messages_empty_does_nothing():
    mock = make_mock_client()
    with patch("app.services.chat.db_client", mock_managed(mock)):
        from app.services.chat import ChatService
        await ChatService.add_messages([], SESSION_ID, user_jwt=TEST_JWT)
    mock.table.assert_not_called()

@pytest.mark.asyncio
async def test_deactivate_session_calls_update():
    mock = make_mock_client(data=[{"id": SESSION_ID}])
    with patch("app.services.chat.db_client", mock_managed(mock)):
        from app.services.chat import ChatService
        await ChatService.deactivate_session(SESSION_ID, user_id=USER_ID, user_jwt=TEST_JWT)
    mock.update.assert_called_once_with({"is_active": False})
    mock.eq.assert_called_with("id", SESSION_ID)
```

Run:
```bash
cd ai_engine && pytest tests/test_chat_service.py -v
```

All 4 tests must pass before continuing.

---

18. `ai_engine/app/services/sessions.py`
    In-memory session store (`SessionStore`). `AgentSession` dataclass holds the
    OpenHands conversation object, event buffer (asyncio.Queue), workspace path,
    `container_id` (Docker sandbox), and metadata. `create_session` creates a
    workspace directory, calls `docker_manager.create_sandbox()` (graceful fallback
    if Docker is unavailable), and builds a `LocalConversation` with `DockerWorkspace`
    (when the SDK exports it) or `LocalWorkspace`. `destroy_session` closes the
    conversation, calls `docker_manager.destroy_container()`, and removes the
    workspace directory.

19. `ai_engine/app/services/docker_workspace.py`
    `DockerSessionManager` — wraps the Docker SDK for full sandbox lifecycle.
    `create_sandbox(session_id, user_id, workspace_dir)` — creates an isolated
    container per session: binds the workspace directory at `WORKSPACE_MOUNT_PATH`
    (`/workspace`), applies `SANDBOX_MEMORY_LIMIT` and `SANDBOX_CPU_LIMIT`, labels
    with `lucid.managed=true` / `lucid.session_id` / `lucid.user_id`. Resolves the
    host-side bind-mount path via `HOST_WORKSPACE_PATH` (DinD support). Returns
    the container ID. `destroy_container(container_id, session_id)` — stops and
    removes a specific container. `is_docker_available` (startup health check),
    `cleanup_orphaned_containers` (removes leftover `lucid.managed=true` containers
    on startup), `destroy_all` (called on shutdown).

20. `ai_engine/app/services/llm.py`
    LiteLLM routing helper. Selects the correct model config based on
    `model_provider` string and builds kwargs for the OpenHands SDK.

21. `ai_engine/app/services/integrations.py`
    Two sections:
    - Encryption helpers: `encrypt_token` / `decrypt_token` (AES-256-CBC,
      key = SHA-256(ENCRYPTION_KEY)).
    - DB helpers using Supabase: `upsert_integration`, `get_integration`,
      `delete_integration`, `list_integrations`. All accept `user_jwt: str | None`
      and use `db_client(user_jwt)`. All wrap DB calls in APIError + Exception
      handlers. The `integrations` table currently uses camelCase columns
      (`userId`, `accessTokenEncrypted`, etc.).
    - GitHub API: `github_get_user`, `github_list_repos`, `github_create_pr`.
    - GitLab API: `gitlab_get_user`, `gitlab_list_repos`, `gitlab_create_mr`.

### Tests — `integrations.py` (encryption)

**`ai_engine/tests/test_encryption.py`** — pure unit tests, no mocking needed:

```python
import os
import pytest
from unittest.mock import patch

def test_encrypt_decrypt_round_trip():
    with patch.dict(os.environ, {"ENCRYPTION_KEY": "test_key_32chars_minimum_length!"}):
        from app.services.integrations import encrypt_token, decrypt_token
        token = "ghp_supersecrettoken12345"
        enc, iv = encrypt_token(token)
        assert enc != token
        assert len(iv) == 32      # 16 bytes as hex = 32 chars
        assert decrypt_token(enc, iv) == token

def test_different_tokens_produce_different_ciphertext():
    with patch.dict(os.environ, {"ENCRYPTION_KEY": "test_key_32chars_minimum_length!"}):
        from app.services.integrations import encrypt_token
        enc1, _ = encrypt_token("token_aaa")
        enc2, _ = encrypt_token("token_bbb")
        assert enc1 != enc2

def test_same_token_different_iv_each_call():
    """Each encryption call uses a fresh random IV."""
    with patch.dict(os.environ, {"ENCRYPTION_KEY": "test_key_32chars_minimum_length!"}):
        from app.services.integrations import encrypt_token
        _, iv1 = encrypt_token("same_token")
        _, iv2 = encrypt_token("same_token")
        assert iv1 != iv2

def test_missing_encryption_key_raises():
    import app.config as cfg
    old = cfg.settings.ENCRYPTION_KEY
    cfg.settings.ENCRYPTION_KEY = ""
    try:
        from app.services.integrations import encrypt_token
        with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
            encrypt_token("anything")
    finally:
        cfg.settings.ENCRYPTION_KEY = old
```

Run:
```bash
cd ai_engine && pytest tests/test_encryption.py -v
```

All 4 tests must pass before continuing.

---

## Phase 5 — AI Engine: event streaming

22. `ai_engine/app/events.py`
    - `now_iso()` — current UTC as ISO-8601 string.
    - `format_sdk_event(event)` — converts an OpenHands SDK event object into a
      JSON-serialisable dict for WebSocket transmission.
    - `_flush_batch(batch, chat_session_id, user_jwt)` — calls `ChatService.add_messages`
      to batch-insert events into Supabase (`user_jwt: str | None`).
    - `stream_events_to_ws(websocket, session, chat_session_id, user_jwt)` — background
      task that drains `session.event_buffer`, forwards to WS, and flushes to DB
      every `DB_BATCH_SIZE` events or `DB_BATCH_INTERVAL` seconds.

### Tests — Phase 5

**`ai_engine/tests/test_events.py`**:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

TEST_JWT = "test.jwt.token"

@pytest.mark.asyncio
async def test_flush_batch_calls_add_messages():
    with patch("app.events.ChatService.add_messages", new_callable=AsyncMock) as mock_add:
        from app.events import _flush_batch
        batch = [
            {"content": "hello", "eventType": "ThinkAction", "event": "action"},
            {"content": "world", "eventType": "CmdRunAction", "event": "action"},
        ]
        await _flush_batch(batch, "session-123", TEST_JWT)
        mock_add.assert_called_once_with(batch, "session-123", user_jwt=TEST_JWT)

@pytest.mark.asyncio
async def test_flush_batch_empty_does_nothing():
    with patch("app.events.ChatService.add_messages", new_callable=AsyncMock) as mock_add:
        from app.events import _flush_batch
        await _flush_batch([], "session-123", TEST_JWT)
        mock_add.assert_not_called()

@pytest.mark.asyncio
async def test_flush_batch_logs_on_error(caplog):
    import logging
    with patch("app.events.ChatService.add_messages",
               new_callable=AsyncMock, side_effect=Exception("DB down")):
        from app.events import _flush_batch
        with caplog.at_level(logging.WARNING):
            await _flush_batch([{"content": "x", "eventType": "T"}], "sid", TEST_JWT)
        assert "Failed to flush" in caplog.text

def test_format_sdk_event_action():
    from app.events import format_sdk_event

    class FakeAction:
        pass
    FakeAction.__name__ = "CmdRunAction"
    event = FakeAction()
    event.content = "ls -la"
    event.command = "ls -la"

    result = format_sdk_event(event)
    assert result is not None
    assert result["event"] == "action"
    assert result["eventType"] == "CmdRunAction"
    assert result["content"] == "ls -la"
    assert result["command"] == "ls -la"
    assert "timestamp" in result

def test_format_sdk_event_truncates_long_content():
    from app.events import format_sdk_event
    from app.config import WS_EVENT_MAX_CHARS

    class FakeObs:
        pass
    FakeObs.__name__ = "CmdOutputObservation"
    e = FakeObs()
    e.content = "x" * (WS_EVENT_MAX_CHARS + 500)

    result = format_sdk_event(e)
    assert len(result["content"]) == WS_EVENT_MAX_CHARS
```

Run:
```bash
cd ai_engine && pytest tests/test_events.py -v
```

All 5 tests must pass before continuing.

---

## Phase 6 — AI Engine: routers

23. `ai_engine/app/routers/__init__.py`
    Empty (or re-exports routers for clean imports in `app/__init__.py`).

24. `ai_engine/app/routers/health.py`
    `GET /health` — returns `{"status": "ok"}`. No auth required.

### Tests — health router

**`ai_engine/tests/test_health.py`**:

```python
import pytest

@pytest.mark.asyncio
async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

Run:
```bash
cd ai_engine && pytest tests/test_health.py -v
```

---

25. `ai_engine/app/routers/sessions.py`
    REST endpoints for in-memory agent sessions:
    `GET /api/v1/sessions`, `GET /api/v1/sessions/{id}`, `DELETE /api/v1/sessions/{id}`.

### Tests — sessions router

**`ai_engine/tests/test_sessions.py`**:

```python
import pytest
import datetime
from jose import jwt
from app.config import settings

@pytest.mark.asyncio
async def test_create_session(client, auth_headers):
    """Session creation should return a sessionId."""
    resp = await client.post("/api/v1/sessions",
                             headers=auth_headers,
                             json={"task": "Write hello world"})
    assert resp.status_code == 200
    data = resp.json()
    assert "sessionId" in data
    assert data["status"] in ("mock", "ready")

@pytest.mark.asyncio
async def test_list_sessions_own_only(client, auth_headers):
    """List returns only sessions belonging to the authenticated user."""
    resp = await client.post("/api/v1/sessions",
                             headers=auth_headers,
                             json={"task": "Task A"})
    session_id = resp.json()["sessionId"]

    resp = await client.get("/api/v1/sessions", headers=auth_headers)
    assert resp.status_code == 200
    ids = [s["sessionId"] for s in resp.json()["sessions"]]
    assert session_id in ids

@pytest.mark.asyncio
async def test_stop_session(client, auth_headers):
    resp = await client.post("/api/v1/sessions",
                             headers=auth_headers,
                             json={"task": "Task B"})
    session_id = resp.json()["sessionId"]

    resp = await client.delete(f"/api/v1/sessions/{session_id}",
                               headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"

@pytest.mark.asyncio
async def test_stop_nonexistent_session(client, auth_headers):
    resp = await client.delete("/api/v1/sessions/nonexistent-id",
                               headers=auth_headers)
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_stop_other_users_session(client, auth_headers):
    """A user cannot stop another user's session."""
    resp = await client.post("/api/v1/sessions",
                             headers=auth_headers,
                             json={"task": "User A task"})
    session_id = resp.json()["sessionId"]

    token_b = jwt.encode(
        {"sub": "other-user-id", "role": "authenticated",
         "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)},
        settings.SUPABASE_JWT_SECRET, algorithm="HS256",
    )
    resp = await client.delete(f"/api/v1/sessions/{session_id}",
                               headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 403
```

Run:
```bash
cd ai_engine && pytest tests/test_sessions.py -v
```

All 5 tests must pass before continuing.

---

26. `ai_engine/app/routers/chat.py`
    Chat history CRUD — calls `ChatService` directly:
    - `GET /api/v1/chats` — paginated list of the user's sessions.
    - `GET /api/v1/chats/{chat_id}` — session with all messages.
    - `DELETE /api/v1/chats/{chat_id}`
    - `PATCH /api/v1/chats/{chat_id}` — rename.

### Tests — chat router

**`ai_engine/tests/test_chats.py`**:

```python
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock

CHAT_ID = str(uuid.uuid4())
FAKE_SESSION = {
    "id": CHAT_ID, "user_id": "test-user-uuid-0001",
    "title": "My chat", "is_active": True,
    "agent_session_id": None, "project_id": None,
    "model_provider": "anthropic", "created_at": "2024-01-01T00:00:00+00:00",
    "updated_at": "2024-01-01T00:00:00+00:00",
}

@pytest.mark.asyncio
async def test_list_chats_empty(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=[]))
    resp = await client.get("/api/v1/chats", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"chats": []}

@pytest.mark.asyncio
async def test_list_chats_returns_rows(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=[FAKE_SESSION]))
    resp = await client.get("/api/v1/chats", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["chats"]) == 1
    assert resp.json()["chats"][0]["id"] == CHAT_ID

@pytest.mark.asyncio
async def test_get_chat_not_found(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=None))
    resp = await client.get(f"/api/v1/chats/{CHAT_ID}", headers=auth_headers)
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_get_chat_with_messages(client, auth_headers, patch_supabase):
    session_with_msgs = {**FAKE_SESSION, "chat_messages": [
        {"id": str(uuid.uuid4()), "role": "user", "content": "hello",
         "event_type": "InitialTask", "metadata_json": None,
         "created_at": "2024-01-01T00:00:01+00:00"},
    ]}
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=session_with_msgs))
    resp = await client.get(f"/api/v1/chats/{CHAT_ID}", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["messages"]) == 1

@pytest.mark.asyncio
async def test_rename_chat(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=[FAKE_SESSION]))
    resp = await client.patch(f"/api/v1/chats/{CHAT_ID}",
                              headers=auth_headers,
                              json={"title": "New title"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New title"

@pytest.mark.asyncio
async def test_rename_chat_missing_title(client, auth_headers, patch_supabase):
    resp = await client.patch(f"/api/v1/chats/{CHAT_ID}",
                              headers=auth_headers, json={})
    assert resp.status_code == 400

@pytest.mark.asyncio
async def test_delete_chat(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=[FAKE_SESSION]))
    resp = await client.delete(f"/api/v1/chats/{CHAT_ID}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

@pytest.mark.asyncio
async def test_delete_chat_not_found(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=[]))
    resp = await client.delete(f"/api/v1/chats/{CHAT_ID}", headers=auth_headers)
    assert resp.status_code == 404
```

Run:
```bash
cd ai_engine && pytest tests/test_chats.py -v
```

All 8 tests must pass before continuing.

---

27. `ai_engine/app/routers/files.py`
    Workspace file-tree endpoints (prefix `/api/v1/files`, session identified by query param):
    - `GET /api/v1/files/list?session_id=<id>` — returns recursive directory tree.
    - `GET /api/v1/files/read?session_id=<id>&path=<path>` — returns file contents.
    Also exports `should_refresh_file_tree(event)` and `build_file_tree(session)`
    used by `events.py` to push tree updates after file-write events.

### Verify — files router

Manual smoke test (requires a running session):
```bash
curl -H "Authorization: Bearer <jwt>" \
  "http://localhost:8000/api/v1/files/list?session_id=<session_id>"
# → { "tree": [...] }

curl -H "Authorization: Bearer <jwt>" \
  "http://localhost:8000/api/v1/files/read?session_id=<session_id>&path=/workspace/hello.py"
# → { "content": "..." }
```

---

28. `ai_engine/app/routers/ws.py`
    `WebSocket /api/v1/ws` — the main real-time channel.
    1. Accept + authenticate (query param JWT or handshake message).
    2. Receive initial `{task, repoUrl, gitToken, modelProvider, ...}`.
    3. Call `create_session` → Docker sandbox.
    4. Persist `chat_sessions` row via `ChatService.create_session`.
    5. If SDK unavailable → run `_run_mock_loop`.
    6. Else → start `stream_events_to_ws` background task, call
       `session.conversation.run()` with timeout, handle follow-up messages.
    7. `finally` → cancel streaming task, `destroy_session`, `ChatService.deactivate_session`.

### Verify — WebSocket

Manual test using `websocat` or the browser dev tools:
```bash
websocat ws://localhost:8000/api/v1/ws?token=<jwt>
# send: {"task": "print hello world"}
# expect stream of agent_event messages, then {"type":"status","status":"completed"}
```

Confirm in Supabase Dashboard:
- A new row in `chat_sessions` with `is_active = true` during the session.
- After disconnect: `is_active = false`.
- Rows in `chat_messages` for the user message and agent events.

---

29. `ai_engine/app/routers/integrations.py`
    Git provider PAT management — calls service functions directly:
    - `POST /api/v1/integrations` — validate + save PAT.
    - `GET /api/v1/integrations` — list connected providers.
    - `DELETE /api/v1/integrations/{provider}` — disconnect.
    - `GET /api/v1/integrations/{provider}/repos` — list repos via stored PAT.
    - `POST /api/v1/integrations/{provider}/pr` — open PR / MR.

### Tests — integrations router

**`ai_engine/tests/test_integrations.py`**:

```python
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_save_integration_validates_token(client, auth_headers, patch_supabase):
    """Bad token → validation fails → 422."""
    with patch("app.services.integrations.github_get_user",
               side_effect=Exception("401 Unauthorized")):
        resp = await client.post("/api/v1/integrations",
                                 headers=auth_headers,
                                 json={"provider": "github", "token": "bad_token"})
    assert resp.status_code == 422

@pytest.mark.asyncio
async def test_save_integration_success(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(
        return_value=MagicMock(data=[{"id": str(uuid.uuid4())}])
    )
    with patch("app.services.integrations.github_get_user",
               return_value={"login": "octocat", "name": "Octocat"}):
        resp = await client.post("/api/v1/integrations",
                                 headers=auth_headers,
                                 json={"provider": "github", "token": "ghp_valid"})
    assert resp.status_code == 200
    assert resp.json()["provider"] == "github"
    assert resp.json()["username"] == "octocat"

@pytest.mark.asyncio
async def test_list_integrations_empty(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=[]))
    resp = await client.get("/api/v1/integrations", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["integrations"] == []

@pytest.mark.asyncio
async def test_delete_integration(client, auth_headers, patch_supabase):
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=[{"id": "1"}]))
    resp = await client.delete("/api/v1/integrations/github", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "disconnected"

@pytest.mark.asyncio
async def test_list_repos_no_integration(client, auth_headers, patch_supabase):
    """No stored PAT → 404."""
    patch_supabase.execute = AsyncMock(return_value=MagicMock(data=None))
    resp = await client.get("/api/v1/integrations/github/repos", headers=auth_headers)
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_gitlab_url_trailing_slash_stripped(client, auth_headers, patch_supabase):
    """gitlabUrl with trailing slash must not produce double-slash in API calls."""
    calls = []

    async def mock_get_user(token, *, gitlab_url):
        calls.append(gitlab_url)
        return {"username": "gl_user"}

    patch_supabase.execute = AsyncMock(
        return_value=MagicMock(data=[{"id": str(uuid.uuid4())}])
    )
    with patch("app.services.integrations.gitlab_get_user", side_effect=mock_get_user):
        await client.post("/api/v1/integrations",
                          headers=auth_headers,
                          json={"provider": "gitlab", "token": "glpat_x",
                                "gitlabUrl": "https://gitlab.example.com/"})

    assert calls[0] == "https://gitlab.example.com"
    assert not calls[0].endswith("/")
```

Run:
```bash
cd ai_engine && pytest tests/test_integrations.py -v
```

All 6 tests must pass before continuing.

---

## Phase 7 — Docker orchestration

30. `docker-compose.yml`
    One `ai_engine` service (port 8000).
    All secrets (`INTERNAL_API_KEY`, `ENCRYPTION_KEY`, `SESSION_SECRET`, `AUTH_SECRET`)
    must use `${VAR}` references — never hardcoded values.
    Env vars: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`,
    `SUPABASE_SERVICE_KEY`, `ENCRYPTION_KEY`, `INTERNAL_API_KEY`, LLM keys
    (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`), `DEFAULT_MODEL_PROVIDER`, `MAX_ITERATIONS`,
    `HOST_WORKSPACE_PATH` (left-hand side of the workspace bind mount — needed so the
    Docker daemon can resolve host-side paths when creating per-session sandbox containers).
    Use a **bind mount** — not a named volume — for the workspace directory:
    `${PWD}/workspaces:/app/storage`. This is required so the Docker daemon can resolve
    the host-side path for sandbox container bind mounts (named volumes are opaque to
    the daemon and cannot be used here).
    No `db` service — Postgres is hosted on Supabase.

### Verify Phase 7

Create a root `.env` file with all required vars (see Phase 8 step 3 for the full list),
then:
```bash
docker-compose up --build
```

Expected:
- No build errors.
- ai_engine starts and logs show Supabase connection info (no missing env var errors).
- `curl http://localhost:8000/health` → `{"status": "ok"}`.

---

## Run all tests

After Phase 7, run the full test suite to confirm nothing regressed:

```bash
cd ai_engine && pytest tests/ -v
```

Expected output: all tests pass. Fix any failures before the first run checklist.

---

## Phase 8 — First run checklist

1. Create OAuth apps and configure them in Supabase Dashboard → Authentication → Providers
   (see Phase 1 step 3 for full instructions per provider).

2. In Supabase Dashboard → Authentication → URL Configuration set **Site URL** to your
   app's root URL.

3. Set env vars in root `.env` (loaded by docker-compose):
   ```
   SUPABASE_URL=https://<ref>.supabase.co
   SUPABASE_ANON_KEY=...
   SUPABASE_JWT_SECRET=...      # Dashboard → Settings → API → JWT Settings
   SUPABASE_SERVICE_KEY=...
   ENCRYPTION_KEY=...           # openssl rand -hex 32
   INTERNAL_API_KEY=...         # openssl rand -hex 32
   SESSION_SECRET=...           # openssl rand -hex 32
   AUTH_SECRET=...              # openssl rand -hex 32
   ANTHROPIC_API_KEY=...        # or GOOGLE_API_KEY
   DEFAULT_MODEL_PROVIDER=anthropic
   ```

4. Apply migrations in Supabase Dashboard → SQL Editor → New query, running each
   file in order:
   - `supabase/migrations/001_initial_schema.sql`
   - `supabase/migrations/002_integrations.sql`

   This creates `users` (with `handle_new_user` trigger), `chat_sessions`,
   `chat_messages` + all RLS policies.

5. Start:
   ```bash
   docker-compose up
   ```

6. Verify: `GET http://localhost:8000/health` → `{"status":"ok"}`

7. Sign in via Google, GitHub, or GitLab — confirm a row appears in `auth.users`
   and a corresponding profile row in `public.users` (created automatically by the trigger).

8. Connect a GitHub or GitLab PAT:
   ```bash
   curl -X POST http://localhost:8000/api/v1/integrations \
     -H "Authorization: Bearer <jwt>" \
     -H "Content-Type: application/json" \
     -d '{"provider":"github","token":"ghp_..."}'
   # → {"provider":"github","username":"...","connected":true}
   ```

9. Start a chat session via WebSocket and confirm `chat_sessions` and `chat_messages`
   rows appear in the Supabase Dashboard.
