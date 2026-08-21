#!/usr/bin/env python
"""Evaluation harness for the required assessment cases (see docs/EVALUATION.md).

This script exercises ONLY production code paths (repositories, authority
resolution, calculations, conflict detection) against the record IDs named
in the assessment. Those IDs appear here and in tests/ ONLY -- never in
app/ production logic, which loads and reasons over the workbook/PDFs
generically. Run with no ANTHROPIC_API_KEY required.

Usage: python scripts/evaluate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.principal import DEMO_USERS
from app.db import get_connection, init_schema
from app.domain.authority import resolve_cancellation_rule, resolve_service_credit_rule, resolve_support_sla
from app.domain.calculations import evaluate_cancellation, evaluate_service_credit, evaluate_ticket_sla
from app.domain.conflicts import detect_historical_conflict
from app.domain.repositories import Repositories
from app.domain.snapshot import get_snapshot
from app.ingestion.loader import run_ingestion

ADMIN = DEMO_USERS["u_admin"]
PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not condition else ""))


def main() -> int:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    conn = get_connection(Path(":memory:"))
    init_schema(conn)
    run_ingestion(conn, data_dir)
    from app.retrieval.retriever import build_index
    build_index(conn)

    repos = Repositories.build(conn)
    now = get_snapshot()
    print(f"Dataset snapshot: {now.isoformat()}\n")

    def cancel_eval(order_id):
        order = repos.orders.get(ADMIN, order_id)
        account = repos.accounts.get(ADMIN, order.account_id)
        rule = resolve_cancellation_rule(conn, account)
        return evaluate_cancellation(order, rule, now)

    def credit_eval(order_id):
        order = repos.orders.get(ADMIN, order_id)
        account = repos.accounts.get(ADMIN, order.account_id)
        rule = resolve_service_credit_rule(conn, account)
        return evaluate_service_credit(order, rule, now)

    print("--- Cancellation cases ---")
    ev = cancel_eval("ORD-1001")
    check("ORD-1001 eligible with no fee (Northstar override)", ev.eligible is True and ev.fee_inr == 0)

    ev = cancel_eval("ORD-1002")
    check("ORD-1002 cannot cancel, recommend return-to-origin", ev.eligible is False and ev.recommend_return_to_origin)

    ev = cancel_eval("ORD-2001")
    check("ORD-2001 fee = INR 250 (LumenWorks, no waiver)", ev.eligible is True and ev.fee_inr == 250)

    ev = cancel_eval("ORD-3001")
    check("ORD-3001 no fee (within 30 min)", ev.eligible is True and ev.fee_inr == 0)

    ev = cancel_eval("ORD-4001")
    check("ORD-4001 cannot cancel (DELIVERED)", ev.eligible is False)

    print("\n--- Failed pickup / credit case ---")
    ev = credit_eval("ORD-2002")
    check(
        "ORD-2002 eligible, fixed INR 300 (LumenWorks override, not 10% default)",
        ev.eligible is True and ev.amount_inr == 300,
    )

    print("\n--- Support tickets ---")
    t = repos.tickets.get(ADMIN, "TKT-501")
    acc = repos.accounts.get(ADMIN, t.account_id)
    sla = resolve_support_sla(conn, acc, "P1")
    tev = evaluate_ticket_sla(t, sla, now)
    check("TKT-501 Northstar P1 target = 15 min, 24x7 (agreement override)", sla.target_value == 15 and sla.is_24x7)
    check("TKT-501 age ~= 30 min", abs(tev.age_minutes - 30) < 1)
    check("TKT-501 breach never claimed as confirmed", tev.breach_confirmed is False)

    t = repos.tickets.get(ADMIN, "TKT-505")
    acc = repos.accounts.get(ADMIN, t.account_id)
    sla = resolve_support_sla(conn, acc, "P1")
    tev = evaluate_ticket_sla(t, sla, now)
    check("TKT-505 Axis Labs default Enterprise P1 = 30 min, 24x7", sla.target_value == 30 and sla.is_24x7)
    check("TKT-505 age ~= 150 min (2h30)", abs(tev.age_minutes - 150) < 1)
    check("TKT-505 breach never claimed as confirmed", tev.breach_confirmed is False)

    print("\n--- Poisoned historical-ticket cases ---")
    t = repos.tickets.get(ADMIN, "TKT-450")
    acc = repos.accounts.get(ADMIN, t.account_id)
    conflict = detect_historical_conflict(t, acc, conn)
    check("TKT-450 historical INR250 claim flagged as conflicting", conflict is not None and conflict.domain == "cancellation")
    ev = cancel_eval("ORD-1001")
    check("Current answer for Northstar cancellation ignores historical claim (fee=0)", ev.fee_inr == 0)

    t = repos.tickets.get(ADMIN, "TKT-451")
    acc = repos.accounts.get(ADMIN, t.account_id)
    conflict = detect_historical_conflict(t, acc, conn)
    check(
        "TKT-451 historical '3000 row limit' claim flagged as conflicting",
        conflict is not None and conflict.domain == "product_capability",
    )
    limit_row = conn.execute(
        """SELECT pr.target_value FROM policy_rules pr JOIN source_documents sd ON sd.file_name = pr.source_file
           WHERE sd.status='current' AND pr.notes='bulk_upload_supported_row_limit'"""
    ).fetchone()
    check("Current Bulk Upload supported limit remains 5000 rows", limit_row["target_value"] == 5000)

    print("\n--- Deprecated-policy trap ---")
    acc3 = repos.accounts.get(ADMIN, "ACCT-003")  # Standard plan, no custom agreement
    sla = resolve_support_sla(conn, acc3, "P1")
    check("Growth/Standard SLA never resolves from deprecated v2", sla.citation is None or "DEPRECATED" not in sla.citation.source_file)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
