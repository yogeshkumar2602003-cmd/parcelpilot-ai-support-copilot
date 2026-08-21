from __future__ import annotations

import pytest

from app.auth.principal import DEMO_USERS
from app.domain.repositories import AuthorizationError, NotFoundError


@pytest.fixture()
def northstar_customer():
    return DEMO_USERS["u_cust_northstar"]


@pytest.fixture()
def lumenworks_customer():
    return DEMO_USERS["u_cust_lumenworks"]


@pytest.fixture()
def support_agent():
    return DEMO_USERS["u_support_rohit"]


def test_customer_can_read_own_order(repos, northstar_customer):
    order = repos.orders.get(northstar_customer, "ORD-1001")
    assert order.account_id == "ACCT-001"


def test_customer_cannot_read_other_account_order(repos, lumenworks_customer):
    with pytest.raises(AuthorizationError):
        repos.orders.get(lumenworks_customer, "ORD-1001")  # belongs to Northstar


def test_customer_cannot_read_other_account_ticket(repos, lumenworks_customer):
    with pytest.raises(AuthorizationError):
        repos.tickets.get(lumenworks_customer, "TKT-501")  # belongs to Northstar


def test_customer_cannot_read_other_account(repos, northstar_customer):
    with pytest.raises(AuthorizationError):
        repos.accounts.get(northstar_customer, "ACCT-002")


def test_customer_search_orders_forced_to_own_account(repos, northstar_customer):
    orders = repos.orders.search(northstar_customer)  # no account_id given
    assert all(o.account_id == "ACCT-001" for o in orders)
    assert len(orders) == 2  # ORD-1001, ORD-1002


def test_customer_cannot_widen_search_via_explicit_account_id(repos, northstar_customer):
    with pytest.raises(AuthorizationError):
        repos.orders.search(northstar_customer, account_id="ACCT-002")


def test_customer_ticket_view_hides_internal_fields(repos, northstar_customer):
    from app.domain.models import TicketPublic

    ticket = repos.tickets.get_scoped(northstar_customer, "TKT-501")
    assert isinstance(ticket, TicketPublic)
    assert not hasattr(ticket, "assigned_to")
    assert not hasattr(ticket, "historical_resolution")


def test_internal_user_can_read_any_account(repos, support_agent):
    order = repos.orders.get(support_agent, "ORD-1001")
    assert order.account_id == "ACCT-001"
    order2 = repos.orders.get(support_agent, "ORD-2001")
    assert order2.account_id == "ACCT-002"


def test_internal_user_ticket_view_includes_internal_fields(repos, support_agent):
    from app.domain.models import Ticket

    ticket = repos.tickets.get_scoped(support_agent, "TKT-501")
    assert isinstance(ticket, Ticket)
    assert ticket.assigned_to == "Rohit"


def test_nonexistent_order_raises_not_found(repos, support_agent):
    with pytest.raises(NotFoundError):
        repos.orders.get(support_agent, "ORD-9999")


def test_unauthorized_does_not_leak_existence_via_error_type(repos, lumenworks_customer):
    # A real order for another account raises AuthorizationError, not NotFoundError,
    # from the repository's perspective -- but the tool layer maps BOTH to a
    # generic denial so a customer cannot distinguish "exists but not yours"
    # from "doesn't exist" in the final response. See test_agent_orchestrator.
    with pytest.raises(AuthorizationError):
        repos.orders.get(lumenworks_customer, "ORD-1001")
