from __future__ import annotations

from app.auth.principal import DEMO_USERS
from app.domain.conflicts import detect_historical_conflict

ADMIN = DEMO_USERS["u_admin"]


def test_tkt_450_historical_cancellation_fee_conflicts_with_northstar_agreement(repos, conn):
    ticket = repos.tickets.get(ADMIN, "TKT-450")
    account = repos.accounts.get(ADMIN, ticket.account_id)
    conflict = detect_historical_conflict(ticket, account, conn)
    assert conflict is not None
    assert conflict.domain == "cancellation"
    answer = conflict.current_authoritative_answer.lower()
    assert "waive" in answer or "no cancellation fee" in answer


def test_tkt_451_historical_bulk_upload_limit_conflicts_with_current_guide(repos, conn):
    ticket = repos.tickets.get(ADMIN, "TKT-451")
    account = repos.accounts.get(ADMIN, ticket.account_id)
    conflict = detect_historical_conflict(ticket, account, conn)
    assert conflict is not None
    assert conflict.domain == "product_capability"
    assert "5000" in conflict.current_authoritative_answer or "5,000" in conflict.current_authoritative_answer


def test_ticket_without_historical_resolution_has_no_conflict(repos, conn):
    ticket = repos.tickets.get(ADMIN, "TKT-501")
    account = repos.accounts.get(ADMIN, ticket.account_id)
    conflict = detect_historical_conflict(ticket, account, conn)
    assert conflict is None
