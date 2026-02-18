"""REST endpoints for chat history."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthenticatedUser, get_current_user
from app.database import get_db
from app.services.chat import ChatService

router = APIRouter(prefix="/api/v1/chats", tags=["chats"])


@router.get("")
async def list_chats(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Paginated list of the user's chat sessions, newest first."""
    sessions = await ChatService.list_sessions(db, user.user_id, limit=limit, offset=offset)
    return {
        "chats": [
            {
                "id": s.id,
                "agentSessionId": s.agent_session_id,
                "projectId": s.project_id,
                "title": s.title,
                "modelProvider": s.model_provider,
                "isActive": s.is_active,
                "createdAt": s.created_at.isoformat() if s.created_at else None,
                "updatedAt": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in sessions
        ]
    }


@router.get("/{chat_id}")
async def get_chat(
    chat_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a chat with all its messages."""
    session = await ChatService.get_session(db, chat_id, user.user_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return {
        "id": session.id,
        "agentSessionId": session.agent_session_id,
        "projectId": session.project_id,
        "title": session.title,
        "modelProvider": session.model_provider,
        "isActive": session.is_active,
        "createdAt": session.created_at.isoformat() if session.created_at else None,
        "updatedAt": session.updated_at.isoformat() if session.updated_at else None,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "eventType": m.event_type,
                "metadataJson": m.metadata_json,
                "createdAt": m.created_at.isoformat() if m.created_at else None,
            }
            for m in sorted(session.messages, key=lambda m: m.created_at)
        ],
    }


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat and all its messages (cascade)."""
    deleted = await ChatService.delete_session(db, chat_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return {"status": "deleted", "id": chat_id}


@router.patch("/{chat_id}")
async def rename_chat(
    chat_id: str,
    body: dict,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a chat."""
    title = body.get("title")
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'title'")
    updated = await ChatService.rename_session(db, chat_id, user.user_id, title)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return {"status": "updated", "id": chat_id, "title": title}
