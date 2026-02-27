"""REST endpoints for chat history."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import AuthenticatedUser, get_current_user
from app.services.chat import ChatService

router = APIRouter(prefix="/api/v1/chats", tags=["chats"])


@router.get("")
async def list_chats(
    user: AuthenticatedUser = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Paginated list of the user's chat sessions, newest first."""
    sessions = await ChatService.list_sessions(
        user.user_id, user_jwt=user.raw_jwt, limit=limit, offset=offset
    )
    return {
        "chats": [
            {
                "id": s["id"],
                "agentSessionId": s.get("agent_session_id"),
                "projectId": s.get("project_id"),
                "title": s.get("title"),
                "modelProvider": s.get("model_provider"),
                "isActive": s.get("is_active"),
                "createdAt": s.get("created_at"),
                "updatedAt": s.get("updated_at"),
            }
            for s in sessions
        ]
    }


@router.get("/{chat_id}")
async def get_chat(
    chat_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Retrieve a chat with all its messages."""
    session = await ChatService.get_session(chat_id, user.user_id, user_jwt=user.raw_jwt)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    messages = session.get("chat_messages") or []
    messages_sorted = sorted(messages, key=lambda m: m.get("created_at") or "")

    return {
        "id": session["id"],
        "agentSessionId": session.get("agent_session_id"),
        "projectId": session.get("project_id"),
        "title": session.get("title"),
        "modelProvider": session.get("model_provider"),
        "isActive": session.get("is_active"),
        "createdAt": session.get("created_at"),
        "updatedAt": session.get("updated_at"),
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "eventType": m.get("event_type"),
                "metadataJson": m.get("metadata_json"),
                "createdAt": m.get("created_at"),
            }
            for m in messages_sorted
        ],
    }


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a chat and all its messages (cascade)."""
    deleted = await ChatService.delete_session(chat_id, user.user_id, user_jwt=user.raw_jwt)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return {"status": "deleted", "id": chat_id}


@router.patch("/{chat_id}")
async def rename_chat(
    chat_id: str,
    body: dict,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Rename a chat."""
    title = body.get("title")
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing 'title'")
    updated = await ChatService.rename_session(
        chat_id, user.user_id, title, user_jwt=user.raw_jwt
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return {"status": "updated", "id": chat_id, "title": title}
