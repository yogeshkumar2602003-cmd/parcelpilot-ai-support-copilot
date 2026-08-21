from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends

from app import config
from app.api.deps import get_conn, get_principal, require_internal
from app.auth.principal import list_demo_users
from app.domain.models import Principal
from app.domain.radar import build_issue_radar
from app.domain.snapshot import get_snapshot_raw

router = APIRouter(prefix="/api", tags=["misc"])


@router.get("/demo-users")
def demo_users():
    return [
        {"user_id": p.user_id, "display_name": p.display_name, "role": p.role, "account_id": p.account_id}
        for p in list_demo_users()
    ]


@router.get("/me")
def me(principal: Principal = Depends(get_principal)):
    return {
        "user_id": principal.user_id, "display_name": principal.display_name,
        "role": principal.role, "account_id": principal.account_id,
        "is_internal": principal.is_internal(),
    }


@router.get("/meta")
def meta(conn: sqlite3.Connection = Depends(get_conn)):
    currency_row = conn.execute("SELECT value FROM meta WHERE key = 'currency'").fetchone()
    doc_count = conn.execute("SELECT COUNT(*) c FROM source_documents").fetchone()["c"]
    return {
        "dataset_snapshot": get_snapshot_raw(),
        "currency": currency_row["value"] if currency_row else "INR",
        "source_documents": doc_count,
        # Boolean only -- see app/config.py Settings.ai_configured. Never the key.
        "ai_configured": config.settings.ai_configured,
    }


@router.get("/issue-radar")
def issue_radar(principal: Principal = Depends(get_principal), conn: sqlite3.Connection = Depends(get_conn)):
    require_internal(principal)
    return build_issue_radar(conn)


@router.get("/audit-log")
def audit_log(
    limit: int = 100, principal: Principal = Depends(get_principal), conn: sqlite3.Connection = Depends(get_conn),
):
    require_internal(principal)
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (min(limit, 500),),
    ).fetchall()
    return [
        {
            "id": r["id"], "timestamp": r["timestamp"], "request_id": r["request_id"],
            "user_id": r["user_id"], "role": r["role"], "account_id": r["account_id"],
            "event_type": r["event_type"], "detail": json.loads(r["detail_json"]) if r["detail_json"] else None,
        }
        for r in rows
    ]


@router.get("/accounts")
def accounts(principal: Principal = Depends(get_principal), conn: sqlite3.Connection = Depends(get_conn)):
    from app.domain.repositories import Repositories

    repos = Repositories.build(conn)
    if principal.is_internal():
        accs = repos.accounts.list_all(principal)
    else:
        own = repos.accounts.get_own(principal)
        accs = [own] if own else []
    return [a.model_dump(mode="json") for a in accs]
