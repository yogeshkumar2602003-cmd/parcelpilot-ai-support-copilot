from __future__ import annotations

import pytest

from app.actions.pending_actions import (
    ActionAuthorizationError,
    ActionNotConfirmableError,
    cancel_action,
    confirm_action,
    propose_action,
)
from app.auth.principal import DEMO_USERS

SUPPORT = DEMO_USERS["u_support_rohit"]
OPS = DEMO_USERS["u_ops_maya"]
CUSTOMER = DEMO_USERS["u_cust_northstar"]
OTHER_CUSTOMER = DEMO_USERS["u_cust_lumenworks"]


def test_propose_action_does_not_mutate_data(conn):
    before = conn.execute("SELECT status FROM tickets WHERE ticket_id = 'TKT-501'").fetchone()["status"]
    propose_action(conn, SUPPORT, "create_escalation", {"ticket_id": "TKT-501", "severity": "P1"}, "outage")
    after = conn.execute("SELECT status FROM tickets WHERE ticket_id = 'TKT-501'").fetchone()["status"]
    assert before == after == "open"


def test_confirm_executes_and_updates_ticket_status(conn):
    action = propose_action(conn, SUPPORT, "create_escalation", {"ticket_id": "TKT-501", "severity": "P1"}, "outage")
    assert action.status == "pending"
    confirmed = confirm_action(conn, SUPPORT, action.action_id)
    assert confirmed.status == "executed"
    row = conn.execute("SELECT status FROM tickets WHERE ticket_id = 'TKT-501'").fetchone()
    assert row["status"] == "escalated"
    esc = conn.execute("SELECT * FROM escalations WHERE action_id = ?", (action.action_id,)).fetchone()
    assert esc is not None


def test_no_mutation_without_confirmation(conn):
    propose_action(conn, SUPPORT, "create_escalation", {"ticket_id": "TKT-501", "severity": "P1"}, "outage")
    row = conn.execute("SELECT status FROM tickets WHERE ticket_id = 'TKT-501'").fetchone()
    assert row["status"] == "open"
    count = conn.execute("SELECT COUNT(*) c FROM escalations").fetchone()["c"]
    assert count == 0


def test_wrong_user_cannot_confirm(conn):
    action = propose_action(conn, SUPPORT, "create_escalation", {"ticket_id": "TKT-501", "severity": "P1"}, "outage")
    with pytest.raises(ActionAuthorizationError):
        confirm_action(conn, OPS, action.action_id)
    fresh = conn.execute("SELECT status FROM pending_actions WHERE action_id = ?", (action.action_id,)).fetchone()
    assert fresh["status"] == "pending"


def test_wrong_user_cannot_cancel(conn):
    action = propose_action(conn, SUPPORT, "create_escalation", {"ticket_id": "TKT-501", "severity": "P1"}, "outage")
    with pytest.raises(ActionAuthorizationError):
        cancel_action(conn, OPS, action.action_id)


def test_confirm_is_idempotent(conn):
    action = propose_action(conn, SUPPORT, "create_followup_task", {"ticket_id": "TKT-502", "description": "follow up"}, "reminder")
    first = confirm_action(conn, SUPPORT, action.action_id)
    second = confirm_action(conn, SUPPORT, action.action_id)
    assert first.result == second.result
    count = conn.execute("SELECT COUNT(*) c FROM followup_tasks WHERE action_id = ?", (action.action_id,)).fetchone()["c"]
    assert count == 1  # not duplicated by the second confirm call


def test_cancelled_action_cannot_be_confirmed(conn):
    action = propose_action(conn, SUPPORT, "create_followup_task", {"ticket_id": "TKT-502", "description": "x"}, "y")
    cancel_action(conn, SUPPORT, action.action_id)
    with pytest.raises(ActionNotConfirmableError):
        confirm_action(conn, SUPPORT, action.action_id)


def test_customer_can_propose_followup_for_own_account(conn):
    action = propose_action(conn, CUSTOMER, "create_followup_task", {"ticket_id": "TKT-501", "description": "call me back"}, "customer request")
    assert action.account_id == "ACCT-001"
    confirmed = confirm_action(conn, CUSTOMER, action.action_id)
    assert confirmed.status == "executed"


def test_customer_cannot_update_ticket_directly(conn):
    with pytest.raises(ActionAuthorizationError):
        propose_action(conn, CUSTOMER, "update_ticket", {"ticket_id": "TKT-501", "status": "closed"}, "trying to close it myself")


def test_customer_cannot_propose_action_for_other_account(conn):
    with pytest.raises(ActionAuthorizationError):
        propose_action(conn, CUSTOMER, "create_followup_task", {"ticket_id": "TKT-502"}, "x", account_id="ACCT-002")
