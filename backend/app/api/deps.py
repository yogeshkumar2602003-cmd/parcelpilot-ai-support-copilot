from __future__ import annotations

import sqlite3
import uuid

from fastapi import Header, HTTPException, Request

from app.auth.principal import UnknownUserError, resolve_principal
from app.db import get_singleton_connection
from app.domain.models import Principal


def get_conn() -> sqlite3.Connection:
    return get_singleton_connection()


def get_principal(x_demo_user: str | None = Header(default=None)) -> Principal:
    if not x_demo_user:
        raise HTTPException(status_code=401, detail="Missing X-Demo-User header. Select a demo user to continue.")
    try:
        return resolve_principal(x_demo_user)
    except UnknownUserError:
        raise HTTPException(status_code=401, detail=f"Unknown demo user id: {x_demo_user}")


def require_internal(principal: Principal) -> None:
    if not principal.is_internal():
        raise HTTPException(status_code=403, detail="This endpoint requires an internal (support/operations/admin) user.")


def new_request_id(request: Request) -> str:
    existing = request.headers.get("X-Request-Id")
    return existing or f"req_{uuid.uuid4().hex[:10]}"
