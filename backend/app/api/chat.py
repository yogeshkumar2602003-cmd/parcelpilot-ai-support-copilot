from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Any

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import config
from app.agent.llm_client import (
    AnthropicLLMClient,
    LLMClient,
    NoAPIKeyError,
    OllamaLLMClient,
    OllamaModelNotFoundError,
    OllamaResponseError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
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


def _build_llm_client() -> LLMClient:
    """Select the LLM backend per `config.settings.ai_provider`. Referencing
    `AnthropicLLMClient`/`OllamaLLMClient` as this module's own global names
    (rather than a factory living in `app.agent.llm_client`) is deliberate:
    tests patch `app.api.chat.AnthropicLLMClient` directly, and that only
    works if the lookup happens in this module's namespace."""
    if config.settings.ai_provider == "ollama":
        return OllamaLLMClient()
    return AnthropicLLMClient()


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

    llm = _build_llm_client()
    try:
        result = run_agent_turn(conn, principal, llm, body.message, history=history, request_id=request_id)
    except NoAPIKeyError as e:
        # Structured, non-secret configuration error -- never includes the
        # key itself (NoAPIKeyError's message only ever names the env var).
        raise HTTPException(status_code=503, detail={"error": "ai_not_configured", "message": str(e)})
    except OllamaUnavailableError as e:
        logger.exception("ollama unavailable request_id=%s", request_id)
        raise HTTPException(status_code=503, detail={"error": "ai_unavailable", "message": str(e)})
    except OllamaModelNotFoundError as e:
        logger.exception("ollama model missing request_id=%s", request_id)
        raise HTTPException(status_code=503, detail={"error": "ai_model_missing", "message": str(e)})
    except OllamaTimeoutError as e:
        logger.exception("ollama timeout request_id=%s", request_id)
        raise HTTPException(status_code=504, detail={"error": "ai_timeout", "message": str(e)})
    except OllamaResponseError:
        logger.exception("ollama response error request_id=%s", request_id)
        raise HTTPException(
            status_code=502,
            detail={"error": "ai_provider_error", "message": "The local AI model returned an unexpected response."},
        )
    except anthropic.APIError:
        # Covers billing/credit failures, auth errors, rate limits, and
        # connectivity problems talking to api.anthropic.com. Never forward
        # the SDK exception's raw text to the client -- it can include
        # request ids / account-identifying details -- only a fixed, safe
        # message. Full detail goes to the server log only.
        logger.exception("anthropic api error request_id=%s", request_id)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "ai_provider_error",
                "message": "The AI provider (Anthropic) rejected the request. This is often a billing/credit or "
                "API key issue -- check the server logs and your Anthropic account, or switch AI_PROVIDER=ollama "
                "for local/free mode.",
            },
        )
    except Exception:
        logger.exception("agent turn failed request_id=%s", request_id)
        raise HTTPException(status_code=500, detail="The agent encountered an internal error processing this request.")

    # Persist the updated raw transcript for this session (best-effort, in-memory).
    new_history = list(history)
    new_history.append({"role": "user", "content": body.message})
    new_history.append({"role": "assistant", "content": result.raw_final_text})
    _SESSIONS[session_id] = new_history[-20:]  # cap transcript length

    return ChatResponse(session_id=session_id, request_id=request_id, answer=result.answer)
