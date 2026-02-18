"""Pydantic request / response models for the REST API."""

from typing import Optional

from pydantic import BaseModel


# ── Requests ────────────────────────────────────────────────

class InitSessionRequest(BaseModel):
    """Payload from the frontend to start an agent session."""

    token: Optional[str] = None
    repoUrl: Optional[str] = None
    gitToken: Optional[str] = None
    branch: Optional[str] = None
    task: str
    projectId: Optional[str] = None
    userId: Optional[str] = None
    model_provider: Optional[str] = None
    api_key: Optional[str] = None
    gitUserName: Optional[str] = None
    gitUserEmail: Optional[str] = None


# ── Responses ───────────────────────────────────────────────

class InitSessionResponse(BaseModel):
    status: str
    sessionId: str
    message: str


class SessionStatus(BaseModel):
    sessionId: str
    userId: str
    task: str
    isAlive: bool
    createdAt: str
    totalEvents: int


class ErrorResponse(BaseModel):
    status: str = "error"
    message: str
