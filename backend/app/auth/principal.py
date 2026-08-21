"""Mock authentication for the assessment demo.

The client never sends a role or account_id -- it sends only an opaque
`user_id` (via the X-Demo-User header). The server resolves that id
against this trusted, server-side registry to build the real `Principal`.
This means a client cannot escalate privilege by tampering with a header
value; the only thing tampering can do is select a *different, still
valid* demo identity, which is the intended demo mechanic.

In a real deployment this module would be replaced by a real identity
provider / session lookup; nothing else in the codebase would need to
change since every authorization check reads from `Principal`.
"""
from __future__ import annotations

from app.domain.models import Principal

INTERNAL_PERMISSIONS = frozenset({
    "read:all_accounts", "read:historical",
    "action:create_escalation", "action:update_ticket", "action:create_followup_task",
})
CUSTOMER_PERMISSIONS = frozenset({"read:own_account", "action:create_followup_task_own", "action:create_escalation_own"})

DEMO_USERS: dict[str, Principal] = {
    "u_support_rohit": Principal(
        user_id="u_support_rohit", display_name="Rohit (Support)", role="support",
        account_id=None, permissions=INTERNAL_PERMISSIONS,
    ),
    "u_ops_maya": Principal(
        user_id="u_ops_maya", display_name="Maya (Operations)", role="operations",
        account_id=None, permissions=INTERNAL_PERMISSIONS,
    ),
    "u_admin": Principal(
        user_id="u_admin", display_name="Admin", role="admin",
        account_id=None, permissions=INTERNAL_PERMISSIONS,
    ),
    "u_cust_northstar": Principal(
        user_id="u_cust_northstar", display_name="Northstar Logistics (Customer)", role="customer",
        account_id="ACCT-001", permissions=CUSTOMER_PERMISSIONS,
    ),
    "u_cust_lumenworks": Principal(
        user_id="u_cust_lumenworks", display_name="LumenWorks (Customer)", role="customer",
        account_id="ACCT-002", permissions=CUSTOMER_PERMISSIONS,
    ),
    "u_cust_beacon": Principal(
        user_id="u_cust_beacon", display_name="Beacon Retail (Customer)", role="customer",
        account_id="ACCT-003", permissions=CUSTOMER_PERMISSIONS,
    ),
    "u_cust_axis": Principal(
        user_id="u_cust_axis", display_name="Axis Labs (Customer)", role="customer",
        account_id="ACCT-004", permissions=CUSTOMER_PERMISSIONS,
    ),
}


class UnknownUserError(Exception):
    pass


def resolve_principal(user_id: str) -> Principal:
    principal = DEMO_USERS.get(user_id)
    if principal is None:
        raise UnknownUserError(f"unknown demo user_id: {user_id}")
    return principal


def list_demo_users() -> list[Principal]:
    return list(DEMO_USERS.values())
