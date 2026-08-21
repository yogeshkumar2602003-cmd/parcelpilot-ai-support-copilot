"""Deterministic business-rule tests covering the assessment's required
evaluation cases (see docs/EVALUATION.md). IDs appear only in this test
file / fixtures, never in production business logic."""
from __future__ import annotations

from app.auth.principal import DEMO_USERS
from app.domain.authority import resolve_cancellation_rule, resolve_service_credit_rule, resolve_support_sla
from app.domain.calculations import evaluate_cancellation, evaluate_service_credit, evaluate_ticket_sla

ADMIN = DEMO_USERS["u_admin"]


def _cancellation_eval(repos, conn, snapshot_now, order_id):
    order = repos.orders.get(ADMIN, order_id)
    account = repos.accounts.get(ADMIN, order.account_id)
    rule = resolve_cancellation_rule(conn, account)
    return evaluate_cancellation(order, rule, snapshot_now)


def _credit_eval(repos, conn, snapshot_now, order_id):
    order = repos.orders.get(ADMIN, order_id)
    account = repos.accounts.get(ADMIN, order.account_id)
    rule = resolve_service_credit_rule(conn, account)
    return evaluate_service_credit(order, rule, snapshot_now)


def test_ord_1001_northstar_booked_no_fee_agreement_override(repos, conn, snapshot_now):
    ev = _cancellation_eval(repos, conn, snapshot_now, "ORD-1001")
    assert ev.eligible is True
    assert ev.fee_inr == 0
    assert ev.rule_source == "agreement_override"


def test_ord_1002_northstar_picked_up_cannot_cancel(repos, conn, snapshot_now):
    ev = _cancellation_eval(repos, conn, snapshot_now, "ORD-1002")
    assert ev.eligible is False
    assert ev.recommend_return_to_origin is True


def test_ord_2001_lumenworks_75min_fee_250(repos, conn, snapshot_now):
    ev = _cancellation_eval(repos, conn, snapshot_now, "ORD-2001")
    assert ev.eligible is True
    assert ev.fee_inr == 250
    assert ev.rule_source == "default_sop"


def test_ord_3001_beacon_15min_no_fee(repos, conn, snapshot_now):
    ev = _cancellation_eval(repos, conn, snapshot_now, "ORD-3001")
    assert ev.eligible is True
    assert ev.fee_inr == 0


def test_ord_4001_delivered_cannot_cancel(repos, conn, snapshot_now):
    ev = _cancellation_eval(repos, conn, snapshot_now, "ORD-4001")
    assert ev.eligible is False
    assert ev.fee_inr is None


def test_ord_2002_lumenworks_credit_fixed_300_not_default_pct(repos, conn, snapshot_now):
    ev = _credit_eval(repos, conn, snapshot_now, "ORD-2002")
    assert ev.eligible is True
    assert ev.amount_inr == 300
    assert ev.rule_source == "agreement_override"
    assert abs(ev.lateness_hours - 4.5) < 0.01


def test_cancellation_strict_boundary_exactly_30_minutes_is_no_fee(repos, conn):
    """'within 30 minutes' is inclusive of the boundary."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.domain.models import Order

    account = repos.accounts.get(ADMIN, "ACCT-003")
    rule = resolve_cancellation_rule(conn, account)
    booked = datetime(2026, 8, 16, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    order = Order(
        order_id="TEST-BOUNDARY", account_id="ACCT-003", carrier="X", status="BOOKED",
        booked_at=booked, cancellation_requested_at=booked.replace(minute=30),
    )
    ev = evaluate_cancellation(order, rule, booked)
    assert ev.fee_inr == 0

    order_over = order.model_copy(update={"cancellation_requested_at": booked.replace(minute=31)})
    ev2 = evaluate_cancellation(order_over, rule, booked)
    assert ev2.fee_inr == 250


def test_service_credit_missing_facts_never_promises_credit(repos, conn, snapshot_now):
    ev = _credit_eval(repos, conn, snapshot_now, "ORD-1001")  # carrier_fault/customer_fault both False -> not eligible
    assert ev.eligible is False


def test_default_service_credit_uses_lower_of_cap_or_percent(conn):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.domain.models import Account, Order

    account = Account(account_id="ACCT-003", account_name="Beacon Retail", plan="Standard", status="active")
    rule = resolve_service_credit_rule(conn, account)
    now = datetime(2026, 8, 16, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    window_end = now - timedelta(hours=3)
    order = Order(
        order_id="TEST-CREDIT", account_id="ACCT-003", carrier="X", status="BOOKED",
        pickup_window_end=window_end, shipment_fee_inr=1200, carrier_fault=True, customer_fault=False,
    )
    ev = evaluate_service_credit(order, rule, now)
    assert ev.eligible is True
    assert ev.amount_inr == min(500, 0.10 * 1200)  # lower of INR 500 or 10% of fee
    assert ev.amount_inr == 120


def test_manager_approval_flagged_above_1000(conn):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.domain.models import Account, Order

    account = Account(account_id="ACCT-999", account_name="Big Shipper", plan="Enterprise", status="active")
    rule = resolve_service_credit_rule(conn, account)
    now = datetime(2026, 8, 16, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    order = Order(
        order_id="TEST-BIG", account_id="ACCT-999", carrier="X", status="BOOKED",
        pickup_window_end=now - timedelta(hours=5), shipment_fee_inr=50000, carrier_fault=True, customer_fault=False,
    )
    ev = evaluate_service_credit(order, rule, now)
    assert ev.eligible is True
    assert ev.amount_inr == 500  # capped, so no manager approval needed here
    assert ev.requires_manager_approval is False


def test_tkt_501_p1_northstar_ticket_age_30min_target_15min(repos, conn, snapshot_now):
    ticket = repos.tickets.get(ADMIN, "TKT-501")
    account = repos.accounts.get(ADMIN, ticket.account_id)
    sla = resolve_support_sla(conn, account, "P1")
    assert sla.source == "agreement_override"
    assert sla.target_value == 15
    ev = evaluate_ticket_sla(ticket, sla, snapshot_now)
    assert abs(ev.age_minutes - 30) < 0.5
    assert ev.potential_breach is True
    assert ev.breach_confirmed is False  # never claim a confirmed breach without first-response data


def test_tkt_505_axis_labs_default_enterprise_p1_2h30_age(repos, conn, snapshot_now):
    ticket = repos.tickets.get(ADMIN, "TKT-505")
    account = repos.accounts.get(ADMIN, ticket.account_id)
    sla = resolve_support_sla(conn, account, "P1")
    assert sla.source == "default_policy"
    assert sla.target_value == 30
    assert sla.target_unit == "minutes"
    ev = evaluate_ticket_sla(ticket, sla, snapshot_now)
    assert abs(ev.age_minutes - 150) < 0.5  # 2h30m
    assert ev.breach_confirmed is False


def test_deprecated_v2_never_used_for_default_support_sla(conn, repos):
    account = repos.accounts.get(ADMIN, "ACCT-003")  # Beacon Retail, Standard, no agreement
    sla = resolve_support_sla(conn, account, "P1")
    # v3 current default for Standard P1 = 4 business_hours; v2 (deprecated) says 8 business_hours.
    assert sla.target_value == 4
    assert sla.target_unit == "business_hours"
    assert sla.citation is not None
    assert sla.citation.status == "current"
    assert "DEPRECATED" not in (sla.citation.source_file or "")


def test_business_hours_targets_are_flagged_as_estimates(repos, conn, snapshot_now):
    ticket = repos.tickets.get(ADMIN, "TKT-502")  # LumenWorks
    account = repos.accounts.get(ADMIN, ticket.account_id)
    sla = resolve_support_sla(conn, account, "P2")  # LumenWorks P2 = 4 business_hours (agreement)
    ev = evaluate_ticket_sla(ticket, sla, snapshot_now)
    assert ev.is_estimate is True
    assert ev.assumption_note is not None


def test_24x7_targets_are_not_flagged_as_estimates(repos, conn, snapshot_now):
    ticket = repos.tickets.get(ADMIN, "TKT-501")  # Northstar P1, 15 min, 24x7
    account = repos.accounts.get(ADMIN, ticket.account_id)
    sla = resolve_support_sla(conn, account, "P1")
    ev = evaluate_ticket_sla(ticket, sla, snapshot_now)
    assert ev.is_estimate is False
