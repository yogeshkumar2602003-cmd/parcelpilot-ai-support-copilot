"""Deterministic, pure-Python business calculations.

Everything here is arithmetic/rule evaluation over already-resolved facts
(order/ticket rows + an app.domain.authority resolution). No LLM call is
involved and no record ID is special-cased -- these functions operate on
whatever Order/Ticket/resolution objects they are given.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.config import DATASET_TZ
from app.domain.authority import CancellationRuleResolution, ServiceCreditRuleResolution, SupportSLAResolution
from app.domain.business_calendar import ASSUMPTION_NOTE, HOURS_PER_BUSINESS_DAY
from app.domain.models import Order, Ticket


def elapsed_minutes(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60


class CancellationEvaluation(BaseModel):
    order_id: str
    eligible: bool | None  # None = cannot determine from available data
    fee_inr: float | None
    currency: str = "INR"
    reason: str
    recommend_return_to_origin: bool = False
    elapsed_minutes_since_booking: float | None = None
    rule_source: str
    missing_facts: list[str] = []


def evaluate_cancellation(order: Order, rule: CancellationRuleResolution, now: datetime) -> CancellationEvaluation:
    missing: list[str] = []

    if order.status == "DRAFT":
        return CancellationEvaluation(
            order_id=order.order_id, eligible=True, fee_inr=0,
            reason="Order is in DRAFT status. DRAFT orders may be cancelled with no fee.",
            rule_source="default_sop",
        )

    if order.status == "PICKED_UP":
        return CancellationEvaluation(
            order_id=order.order_id, eligible=False, fee_inr=None,
            reason="Order has already been PICKED_UP. It cannot be cancelled; use the return-to-origin workflow instead.",
            recommend_return_to_origin=True, rule_source="default_sop",
        )

    if order.status == "DELIVERED":
        return CancellationEvaluation(
            order_id=order.order_id, eligible=False, fee_inr=None,
            reason="Order has already been DELIVERED. Delivered orders cannot be cancelled.",
            rule_source="default_sop",
        )

    if order.status == "CANCELLED":
        return CancellationEvaluation(
            order_id=order.order_id, eligible=False, fee_inr=None,
            reason="Order is already CANCELLED.", rule_source="default_sop",
        )

    if order.status != "BOOKED":
        return CancellationEvaluation(
            order_id=order.order_id, eligible=None, fee_inr=None,
            reason=f"Unrecognized order status '{order.status}' for cancellation evaluation.",
            rule_source="default_sop", missing_facts=["order_status_unrecognized"],
        )

    # BOOKED, not yet PICKED_UP.
    if order.booked_at is None:
        missing.append("booked_at")
    request_time = order.cancellation_requested_at or now
    elapsed = elapsed_minutes(order.booked_at, request_time) if order.booked_at else None

    if rule.fee_waived_override:
        return CancellationEvaluation(
            order_id=order.order_id, eligible=True, fee_inr=0,
            reason="Cancellation fee is waived by the customer's active agreement for any BOOKED shipment before pickup.",
            elapsed_minutes_since_booking=elapsed, rule_source="agreement_override",
        )

    if rule.no_fee_window_minutes is None or rule.fee_after_window_inr is None:
        missing.append("cancellation_sop_rule")

    if missing or elapsed is None:
        return CancellationEvaluation(
            order_id=order.order_id, eligible=True if elapsed is not None else None, fee_inr=None,
            reason="Cancellation is generally permitted for a BOOKED, not-yet-picked-up order, but the fee cannot "
                   "be determined because required data is missing: " + ", ".join(missing or ["booked_at"]),
            elapsed_minutes_since_booking=elapsed, rule_source=rule.source, missing_facts=missing,
        )

    if elapsed <= rule.no_fee_window_minutes:
        fee = 0.0
        reason = (
            f"Cancellation requested {elapsed:.0f} minutes after booking, within the "
            f"{rule.no_fee_window_minutes:.0f}-minute no-fee window. No cancellation fee applies."
        )
    else:
        fee = rule.fee_after_window_inr
        reason = (
            f"Cancellation requested {elapsed:.0f} minutes after booking, after the "
            f"{rule.no_fee_window_minutes:.0f}-minute no-fee window. A cancellation fee of INR {fee:.0f} applies."
        )

    return CancellationEvaluation(
        order_id=order.order_id, eligible=True, fee_inr=fee, reason=reason,
        elapsed_minutes_since_booking=elapsed, rule_source=rule.source,
    )


class ServiceCreditEvaluation(BaseModel):
    order_id: str
    eligible: bool | None
    amount_inr: float | None
    reason: str
    lateness_hours: float | None = None
    requires_manager_approval: bool = False
    monthly_cap_note: str | None = None
    rule_source: str
    missing_facts: list[str] = []


def evaluate_service_credit(order: Order, rule: ServiceCreditRuleResolution, now: datetime) -> ServiceCreditEvaluation:
    missing: list[str] = []
    if order.pickup_window_end is None:
        missing.append("pickup_window_end")
    if order.carrier_fault is None:
        missing.append("carrier_fault")
    if order.customer_fault is None:
        missing.append("customer_fault")
    if rule.threshold_hours is None:
        missing.append("credit_delay_threshold")

    if missing:
        return ServiceCreditEvaluation(
            order_id=order.order_id, eligible=None, amount_inr=None,
            reason="Cannot determine service-credit eligibility: missing " + ", ".join(missing) +
                   ". Do not promise a credit when carrier fault, timing, or customer fault is unknown.",
            rule_source=rule.source, missing_facts=missing,
        )

    reference_time = order.pickup_actual_at or now
    lateness_hours = elapsed_minutes(order.pickup_window_end, reference_time) / 60

    if order.customer_fault:
        return ServiceCreditEvaluation(
            order_id=order.order_id, eligible=False, amount_inr=None,
            reason="A customer-caused issue is recorded for this order, so it is not eligible for a failed-pickup credit.",
            lateness_hours=lateness_hours, rule_source=rule.source,
        )

    if not order.carrier_fault:
        return ServiceCreditEvaluation(
            order_id=order.order_id, eligible=False, amount_inr=None,
            reason="Carrier fault is not recorded for this order, so the default failed-pickup credit rule does not apply.",
            lateness_hours=lateness_hours, rule_source=rule.source,
        )

    if lateness_hours <= rule.threshold_hours:
        return ServiceCreditEvaluation(
            order_id=order.order_id, eligible=False, amount_inr=None,
            reason=f"Pickup is {lateness_hours:.1f} hours past the scheduled window end, which does not exceed the "
                   f"{rule.threshold_hours:.0f}-hour threshold required for a credit (threshold requires strictly more "
                   f"than {rule.threshold_hours:.0f} hours). This may become eligible if the delay continues.",
            lateness_hours=lateness_hours, rule_source=rule.source,
        )

    if rule.amount_is_default:
        if order.shipment_fee_inr is None:
            return ServiceCreditEvaluation(
                order_id=order.order_id, eligible=True, amount_inr=None,
                reason="Eligible for a default failed-pickup credit, but shipment_fee_inr is missing so the 10%-of-fee "
                       "component cannot be computed.",
                lateness_hours=lateness_hours, rule_source=rule.source, missing_facts=["shipment_fee_inr"],
            )
        pct_amount = (rule.default_pct_of_fee or 0) / 100 * order.shipment_fee_inr
        amount = min(rule.default_cap_inr or pct_amount, pct_amount)
    else:
        amount = rule.amount_fixed

    requires_approval = bool(rule.manager_approval_threshold_inr and amount and amount > rule.manager_approval_threshold_inr)

    monthly_cap_note = None
    if rule.monthly_aggregate_cap_inr is not None:
        monthly_cap_note = (
            f"This account's agreement caps monthly aggregate service credits at INR "
            f"{rule.monthly_aggregate_cap_inr:.0f}. The supplied dataset does not include prior credits issued "
            f"this month, so cumulative-cap enforcement cannot be verified from available data."
        )

    reason = (
        f"Pickup is {lateness_hours:.1f} hours past the scheduled window end (threshold: more than "
        f"{rule.threshold_hours:.0f} hours), carrier is at fault, and no customer-caused issue is recorded. "
        f"Eligible for a service credit of INR {amount:.0f}."
    )

    return ServiceCreditEvaluation(
        order_id=order.order_id, eligible=True, amount_inr=amount, reason=reason,
        lateness_hours=lateness_hours, requires_manager_approval=requires_approval,
        monthly_cap_note=monthly_cap_note, rule_source=rule.source,
    )


class TicketSLAEvaluation(BaseModel):
    ticket_id: str
    severity: str
    age_minutes: float
    target_value: float
    target_unit: str
    target_minutes_estimate: float | None
    is_estimate: bool
    assumption_note: str | None
    potential_breach: bool | None
    breach_confirmed: bool
    note: str


def evaluate_ticket_sla(ticket: Ticket, sla: SupportSLAResolution, now: datetime) -> TicketSLAEvaluation:
    age = elapsed_minutes(ticket.created_at, now)

    if sla.target_unit == "minutes":
        target_minutes = sla.target_value
        is_estimate = False
    elif sla.target_unit == "hours":
        target_minutes = sla.target_value * 60
        is_estimate = False
    elif sla.target_unit == "business_hours":
        target_minutes = sla.target_value * 60
        is_estimate = True
    elif sla.target_unit == "business_days":
        target_minutes = sla.target_value * HOURS_PER_BUSINESS_DAY * 60
        is_estimate = True
    else:
        target_minutes = None
        is_estimate = True

    potential_breach = None if target_minutes is None else age > target_minutes
    assumption_note = ASSUMPTION_NOTE.format(tz=DATASET_TZ) if is_estimate else None

    note = (
        f"Ticket age is {age:.0f} minutes and the target is {sla.target_value:g} {sla.target_unit.replace('_', ' ')}"
        f"{' (24x7)' if sla.is_24x7 else ''}. The dataset does not contain a support first-response timestamp, so "
        f"a breach is possible but cannot be confirmed from the supplied data."
    )
    if is_estimate:
        note += " " + assumption_note

    return TicketSLAEvaluation(
        ticket_id=ticket.ticket_id, severity=sla.severity, age_minutes=age,
        target_value=sla.target_value, target_unit=sla.target_unit,
        target_minutes_estimate=target_minutes, is_estimate=is_estimate,
        assumption_note=assumption_note, potential_breach=potential_breach,
        breach_confirmed=False, note=note,
    )
