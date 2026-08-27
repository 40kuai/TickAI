"""LLM chat routes - send message and conversation CRUD.

Reuses hermes.agents.chat.chat() for the tool-call loop and conversation
persistence.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from hermes.agents.chat import chat, chat_stream
from hermes.config import settings as config
from hermes.data.db import session_scope
from hermes.data.models import Conversation

from .deps import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[int] = None


@router.post("")
def send_message(req: ChatRequest, user=Depends(get_current_user)):
    """Send a message to the LLM and return the full response."""
    if not config.LLM_API_KEY():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM is not configured (TOKENHUB_API_KEY not set)",
        )
    try:
        result = chat(req.message, conversation_id=req.conversation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return result


@router.post("/stream")
def stream_message(req: ChatRequest, user=Depends(get_current_user)):
    """Send a message and stream the response via Server-Sent Events.

    Each event is emitted as ``data: {json}\n\n``. The stream terminates with
    ``data: [DONE]\n\n``. Errors raised inside the streaming pipeline are
    forwarded to the client as an SSE ``error`` event.
    """
    if not config.LLM_API_KEY():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM is not configured (TOKENHUB_API_KEY not set)",
        )

    def event_generator():
        try:
            for event in chat_stream(
                req.message, conversation_id=req.conversation_id
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(), media_type="text/event-stream"
    )


@router.get("/conversations")
def list_conversations(user=Depends(get_current_user)):
    """List all conversations, newest first."""
    with session_scope() as s:
        convs = (
            s.query(Conversation)
            .order_by(Conversation.updated_at.desc())
            .all()
        )
        return [
            {
                "id": c.id,
                "title": c.title,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                "total_runs": c.total_runs,
            }
            for c in convs
        ]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user=Depends(get_current_user)):
    """Get a single conversation with all messages."""
    with session_scope() as s:
        c = s.get(Conversation, conversation_id)
        if c is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        messages = json.loads(c.messages_json or "[]")
        return {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "total_runs": c.total_runs,
            "messages": messages,
        }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, user=Depends(get_current_user)):
    """Delete a conversation."""
    with session_scope() as s:
        c = s.get(Conversation, conversation_id)
        if c is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        s.delete(c)
    return {"detail": "Conversation deleted"}
