from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from app.actions.pending_actions import (
    ActionAuthorizationError,
    ActionNotConfirmableError,
    ActionNotFoundError,
    cancel_action,
    confirm_action,
    list_actions,
)
from app.api.deps import get_conn, get_principal, new_request_id
from app.domain.models import PendingAction, Principal

router = APIRouter(prefix="/api/actions", tags=["actions"])


@router.get("", response_model=list[PendingAction])
def get_actions(
    status: str | None = None, principal: Principal = Depends(get_principal), conn: sqlite3.Connection = Depends(get_conn),
) -> list[PendingAction]:
    return list_actions(conn, principal, status=status)


@router.post("/{action_id}/confirm", response_model=PendingAction)
def confirm(
    action_id: str, request: Request,
    principal: Principal = Depends(get_principal), conn: sqlite3.Connection = Depends(get_conn),
) -> PendingAction:
    request_id = new_request_id(request)
    try:
        return confirm_action(conn, principal, action_id, request_id=request_id)
    except ActionNotFoundError:
        raise HTTPException(status_code=404, detail=f"Pending action {action_id} not found.")
    except ActionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ActionNotConfirmableError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{action_id}/cancel", response_model=PendingAction)
def cancel(
    action_id: str, request: Request,
    principal: Principal = Depends(get_principal), conn: sqlite3.Connection = Depends(get_conn),
) -> PendingAction:
    request_id = new_request_id(request)
    try:
        return cancel_action(conn, principal, action_id, request_id=request_id)
    except ActionNotFoundError:
        raise HTTPException(status_code=404, detail=f"Pending action {action_id} not found.")
    except ActionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ActionNotConfirmableError as e:
        raise HTTPException(status_code=409, detail=str(e))
