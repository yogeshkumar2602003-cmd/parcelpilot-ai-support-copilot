from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.agent.llm_client import AnthropicLLMClient, NoAPIKeyError
from app.agent.orchestrator import run_agent_turn
from app.api.deps import get_conn, get_principal, new_request_id
from app.domain.models import AgentAnswer, Principal

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger("parcelpilot.api.chat")

# In-memory per-session raw message history (demo-scale; not persisted across restarts).
_SESSIONS: dict[str, list[dict[str, Any]]] = {}
_SESSION_OWNER: dict[str, str] = {}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    request_id: str
    answer: AgentAnswer


@router.post("", response_model=ChatResponse)
def chat(
    body: ChatRequest, request: Request,
    principal: Principal = Depends(get_principal), conn: sqlite3.Connection = Depends(get_conn),
) -> ChatResponse:
    request_id = new_request_id(request)

    session_id = body.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    if session_id in _SESSION_OWNER and _SESSION_OWNER[session_id] != principal.user_id:
        # Never let one principal's session be reused/read by another.
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
    _SESSION_OWNER[session_id] = principal.user_id
    history = _SESSIONS.get(session_id, [])

    llm = AnthropicLLMClient()
    try:
        result = run_agent_turn(conn, principal, llm, body.message, history=history, request_id=request_id)
    except NoAPIKeyError as e:
        # Structured, non-secret configuration error -- never includes the
        # key itself (NoAPIKeyError's message only ever names the env var).
        raise HTTPException(status_code=503, detail={"error": "ai_not_configured", "message": str(e)})
    except Exception:
        logger.exception("agent turn failed request_id=%s", request_id)
        raise HTTPException(status_code=500, detail="The agent encountered an internal error processing this request.")

    # Persist the updated raw transcript for this session (best-effort, in-memory).
    new_history = list(history)
    new_history.append({"role": "user", "content": body.message})
    new_history.append({"role": "assistant", "content": result.raw_final_text})
    _SESSIONS[session_id] = new_history[-20:]  # cap transcript length

    return ChatResponse(session_id=session_id, request_id=request_id, answer=result.answer)
