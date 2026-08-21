"""Two-phase state-changing action architecture.

Phase 1 (`propose`): the agent (or an API caller) prepares a PendingAction
and persists it with status='pending'. This is the ONLY thing a tool call
can ever do -- there is no code path from an LLM tool call to an executed
mutation.

Phase 2 (`confirm`): a human explicitly calls the confirm endpoint. Only
then does `_execute` run and mutate data. Confirmation is:
  * principal-bound: only the exact user_id that requested the action may
    confirm or cancel it (AuthorizationError otherwise)
  * idempotent: confirming an already-executed action returns the stored
    result without re-executing
  * status-checked: cancelled/expired actions cannot be confirmed

Every transition is written to the audit log.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from app.domain.models import PendingAction, Principal
from app.domain.repositories import TicketRepository

ACTION_TYPES = Literal["create_escalation", "update_ticket", "create_followup_task"]

# Which roles may PROPOSE each action type. Customers may only request an
# escalation or follow-up task for their own account; they may never
# directly update a ticket's internal fields.
ALLOWED_PROPOSERS: dict[str, set[str]] = {
    "create_escalation": {"support", "operations", "admin", "customer"},
    "update_ticket": {"support", "operations", "admin"},
    "create_followup_task": {"support", "operations", "admin", "customer"},
}


class ActionAuthorizationError(Exception):
    pass


class ActionNotFoundError(Exception):
    pass


class ActionNotConfirmableError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_action(row: sqlite3.Row) -> PendingAction:
    return PendingAction(
        action_id=row["action_id"], action_type=row["action_type"],
        payload=json.loads(row["payload_json"]), reason=row["reason"],
        requested_by_user_id=row["requested_by_user_id"], requested_by_role=row["requested_by_role"],
        account_id=row["account_id"], status=row["status"], created_at=row["created_at"],
        decided_at=row["decided_at"], decided_by_user_id=row["decided_by_user_id"],
        result=json.loads(row["result_json"]) if row["result_json"] else None,
    )


def write_audit(conn: sqlite3.Connection, principal: Principal | None, event_type: str, detail: dict[str, Any],
                 request_id: str | None = None) -> None:
    conn.execute(
        """INSERT INTO audit_log (id, timestamp, request_id, user_id, role, account_id, event_type, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"audit_{uuid.uuid4().hex[:12]}", _now_iso(), request_id,
            principal.user_id if principal else None, principal.role if principal else None,
            principal.account_id if principal else None, event_type, json.dumps(detail, default=str),
        ),
    )
    conn.commit()


def propose_action(
    conn: sqlite3.Connection, principal: Principal, action_type: ACTION_TYPES,
    payload: dict[str, Any], reason: str, account_id: str | None = None,
    request_id: str | None = None,
) -> PendingAction:
    if action_type not in ALLOWED_PROPOSERS:
        raise ValueError(f"unknown action_type: {action_type}")
    if principal.role not in ALLOWED_PROPOSERS[action_type]:
        raise ActionAuthorizationError(f"role {principal.role} may not propose {action_type}")

    target_account = account_id or principal.account_id
    if principal.role == "customer":
        if target_account and target_account != principal.account_id:
            raise ActionAuthorizationError("customers may only propose actions for their own account")
        target_account = principal.account_id

    action_id = f"act_{uuid.uuid4().hex[:12]}"
    created_at = _now_iso()
    conn.execute(
        """INSERT INTO pending_actions
           (action_id, action_type, payload_json, reason, requested_by_user_id, requested_by_role,
            account_id, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (
            action_id, action_type, json.dumps(payload), reason, principal.user_id, principal.role,
            target_account, created_at,
        ),
    )
    conn.commit()
    write_audit(conn, principal, "action_proposed", {"action_id": action_id, "action_type": action_type, "payload": payload}, request_id)
    return get_action(conn, action_id)


def get_action(conn: sqlite3.Connection, action_id: str) -> PendingAction:
    row = conn.execute("SELECT * FROM pending_actions WHERE action_id = ?", (action_id,)).fetchone()
    if not row:
        raise ActionNotFoundError(f"pending action {action_id} not found")
    return _row_to_action(row)


def list_actions(conn: sqlite3.Connection, principal: Principal, status: str | None = None) -> list[PendingAction]:
    query = "SELECT * FROM pending_actions WHERE 1=1"
    params: list = []
    if not principal.is_internal():
        query += " AND requested_by_user_id = ?"
        params.append(principal.user_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_action(r) for r in rows]


def _execute(conn: sqlite3.Connection, action: PendingAction) -> dict[str, Any]:
    payload = action.payload
    if action.action_type == "create_escalation":
        esc_id = f"esc_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """INSERT INTO escalations (id, ticket_id, account_id, severity, reason, created_at, created_by_user_id, action_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (esc_id, payload.get("ticket_id"), action.account_id, payload.get("severity"),
             action.reason, _now_iso(), action.requested_by_user_id, action.action_id),
        )
        if payload.get("ticket_id"):
            TicketRepository(conn).update_status_and_assignment(payload["ticket_id"], status="escalated")
        conn.commit()
        return {"escalation_id": esc_id, "ticket_id": payload.get("ticket_id"), "severity": payload.get("severity")}

    if action.action_type == "update_ticket":
        ticket_id = payload["ticket_id"]
        row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        if not row:
            raise ActionNotFoundError(f"ticket {ticket_id} not found")
        old_status, old_assignee = row["status"], row["assigned_to"]
        TicketRepository(conn).update_status_and_assignment(
            ticket_id, status=payload.get("status"), assigned_to=payload.get("assigned_to"),
        )
        update_id = f"upd_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """INSERT INTO ticket_updates (id, ticket_id, field, old_value, new_value, created_at, created_by_user_id, action_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (update_id, ticket_id, "status/assigned_to", f"{old_status}/{old_assignee}",
             f"{payload.get('status', old_status)}/{payload.get('assigned_to', old_assignee)}",
             _now_iso(), action.requested_by_user_id, action.action_id),
        )
        conn.commit()
        return {"ticket_id": ticket_id, "new_status": payload.get("status", old_status),
                 "new_assigned_to": payload.get("assigned_to", old_assignee)}

    if action.action_type == "create_followup_task":
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """INSERT INTO followup_tasks (id, ticket_id, account_id, description, due_at, created_at, created_by_user_id, action_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, payload.get("ticket_id"), action.account_id, payload.get("description"),
             payload.get("due_at"), _now_iso(), action.requested_by_user_id, action.action_id),
        )
        conn.commit()
        return {"task_id": task_id, "description": payload.get("description"), "due_at": payload.get("due_at")}

    raise ValueError(f"no executor for action_type {action.action_type}")


def confirm_action(conn: sqlite3.Connection, principal: Principal, action_id: str,
                    request_id: str | None = None) -> PendingAction:
    action = get_action(conn, action_id)

    if action.requested_by_user_id != principal.user_id:
        write_audit(conn, principal, "action_confirm_denied", {"action_id": action_id, "reason": "wrong_principal"}, request_id)
        raise ActionAuthorizationError("only the requesting user may confirm this action")

    if action.status == "executed":
        write_audit(conn, principal, "action_confirm_idempotent_replay", {"action_id": action_id}, request_id)
        return action  # idempotent: no re-execution

    if action.status != "pending":
        raise ActionNotConfirmableError(f"action {action_id} is '{action.status}' and cannot be confirmed")

    result = _execute(conn, action)
    conn.execute(
        """UPDATE pending_actions SET status = 'executed', decided_at = ?, decided_by_user_id = ?, result_json = ?
           WHERE action_id = ?""",
        (_now_iso(), principal.user_id, json.dumps(result, default=str), action_id),
    )
    conn.commit()
    write_audit(conn, principal, "action_executed", {"action_id": action_id, "result": result}, request_id)
    return get_action(conn, action_id)


def cancel_action(conn: sqlite3.Connection, principal: Principal, action_id: str,
                   request_id: str | None = None) -> PendingAction:
    action = get_action(conn, action_id)
    if action.requested_by_user_id != principal.user_id:
        write_audit(conn, principal, "action_cancel_denied", {"action_id": action_id, "reason": "wrong_principal"}, request_id)
        raise ActionAuthorizationError("only the requesting user may cancel this action")
    if action.status != "pending":
        raise ActionNotConfirmableError(f"action {action_id} is '{action.status}' and cannot be cancelled")
    conn.execute(
        "UPDATE pending_actions SET status = 'cancelled', decided_at = ?, decided_by_user_id = ? WHERE action_id = ?",
        (_now_iso(), principal.user_id, action_id),
    )
    conn.commit()
    write_audit(conn, principal, "action_cancelled", {"action_id": action_id}, request_id)
    return get_action(conn, action_id)
