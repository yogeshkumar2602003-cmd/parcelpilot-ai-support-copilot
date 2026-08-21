"""Orchestrates full, idempotent ingestion of the source pack into SQLite.

Runs at application startup (and can be re-run any time) and is safe to
call repeatedly -- it always fully rebuilds the source-derived tables from
the files on disk, so ingestion state never drifts from the source pack.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

from app.config import DATA_DIR
from app.domain.snapshot import parse_workbook_timestamp, set_snapshot
from app.ingestion.pdf_ingest import authority_category_for, parse_all_pdfs
from app.ingestion.workbook_ingest import parse_workbook

logger = logging.getLogger("parcelpilot.ingestion")


def _chunk_id(file_name: str, idx: int, section: str) -> str:
    digest = hashlib.sha1(f"{file_name}:{idx}:{section}".encode()).hexdigest()[:10]
    return f"chunk_{digest}"


def run_ingestion(conn: sqlite3.Connection, data_dir: Path | None = None) -> dict:
    data_dir = data_dir or DATA_DIR
    from app.db import wipe_operational_tables

    wipe_operational_tables(conn)

    # --- Workbook: accounts, orders, tickets, snapshot ---
    wb_path = data_dir / "ParcelPilot_Assessment_Data.xlsx"
    wb = parse_workbook(wb_path)
    set_snapshot(wb.snapshot_at, wb.snapshot_raw)

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("snapshot_at", wb.snapshot_at.isoformat()),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("snapshot_raw", wb.snapshot_raw),
    )
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", ("currency", wb.currency))

    for a in wb.accounts:
        conn.execute(
            """INSERT OR REPLACE INTO accounts
               (account_id, account_name, plan, status, csm, contract_file, premium_support, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                a.get("account_id"), a.get("account_name"), a.get("plan"), a.get("status"),
                a.get("csm"), a.get("contract_file"), int(bool(a.get("premium_support"))), a.get("notes"),
            ),
        )

    for o in wb.orders:
        conn.execute(
            """INSERT OR REPLACE INTO orders
               (order_id, account_id, carrier, status, booked_at, pickup_window_start, pickup_window_end,
                pickup_actual_at, shipment_fee_inr, carrier_fault, customer_fault, cancellation_requested_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                o.get("order_id"), o.get("account_id"), o.get("carrier"), o.get("status"),
                _iso(o.get("booked_at")), _iso(o.get("pickup_window_start")), _iso(o.get("pickup_window_end")),
                _iso(o.get("pickup_actual_at")), o.get("shipment_fee_inr"),
                _nullable_bool(o.get("carrier_fault")), _nullable_bool(o.get("customer_fault")),
                _iso(o.get("cancellation_requested_at")), o.get("notes"),
            ),
        )

    for t in wb.tickets:
        conn.execute(
            """INSERT OR REPLACE INTO tickets
               (ticket_id, account_id, created_at, status, subject, description, channel,
                assigned_to, last_customer_message_at, historical_resolution)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t.get("ticket_id"), t.get("account_id"), _iso(t.get("created_at")), t.get("status"),
                t.get("subject"), t.get("description"), t.get("channel"), t.get("assigned_to"),
                _iso(t.get("last_customer_message_at")), t.get("historical_resolution"),
            ),
        )

    # --- PDFs: chunks, policy_rules, agreement_terms ---
    docs = parse_all_pdfs(data_dir)
    chunk_count = 0
    rule_count = 0
    for doc in docs:
        authority = authority_category_for(doc.doc_type, doc.status)
        conn.execute(
            """INSERT OR REPLACE INTO source_documents
               (file_name, doc_type, status, authority_category, effective_date, updated_date, customer_scope)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                doc.file_name, doc.doc_type, doc.status, authority,
                doc.effective_date, doc.updated_date, doc.customer_scope,
            ),
        )
        for idx, chunk in enumerate(doc.chunks):
            cid = _chunk_id(doc.file_name, idx, chunk.section)
            conn.execute(
                """INSERT OR REPLACE INTO document_chunks
                   (chunk_id, file_name, doc_type, status, authority_category, effective_date, updated_date,
                    customer_scope, agreement_term_start, agreement_term_end, section, page, text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid, doc.file_name, doc.doc_type, doc.status, authority, doc.effective_date,
                    doc.updated_date, doc.customer_scope, doc.agreement_term_start, doc.agreement_term_end,
                    chunk.section, chunk.page, chunk.text,
                ),
            )
            chunk_count += 1

        for rule in doc.policy_rules:
            conn.execute(
                """INSERT OR REPLACE INTO policy_rules
                   (id, source_file, domain, plan, severity, target_value, target_unit, is_24x7, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule["id"], rule["source_file"], rule["domain"], rule.get("plan"), rule.get("severity"),
                    rule["target_value"], rule["target_unit"], int(bool(rule["is_24x7"])), rule.get("notes"),
                ),
            )
            rule_count += 1

        if doc.agreement_terms:
            t = doc.agreement_terms
            conn.execute(
                """INSERT OR REPLACE INTO agreement_terms
                   (account_id, source_file, status, term_start, term_end,
                    support_p1_value, support_p1_unit, support_p1_24x7,
                    support_p2_value, support_p2_unit, support_p3_value, support_p3_unit,
                    no_weekend_afterhours, cancellation_fee_waived, cancellation_notes,
                    credit_threshold_hours, credit_threshold_strict, credit_amount_fixed,
                    credit_amount_is_default, credit_monthly_cap, csm)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    t["account_id"], t["source_file"], t["status"], t.get("term_start"), t.get("term_end"),
                    t.get("support_p1_value"), t.get("support_p1_unit"), int(bool(t.get("support_p1_24x7"))),
                    t.get("support_p2_value"), t.get("support_p2_unit"),
                    t.get("support_p3_value"), t.get("support_p3_unit"),
                    int(bool(t.get("no_weekend_afterhours"))), int(bool(t.get("cancellation_fee_waived"))),
                    t.get("cancellation_notes"), t.get("credit_threshold_hours"), t.get("credit_threshold_strict"),
                    t.get("credit_amount_fixed"), int(bool(t.get("credit_amount_is_default"))),
                    t.get("credit_monthly_cap"), t.get("csm"),
                ),
            )

    conn.commit()

    summary = {
        "snapshot_at": wb.snapshot_at.isoformat(),
        "accounts": len(wb.accounts),
        "orders": len(wb.orders),
        "tickets": len(wb.tickets),
        "documents": len(docs),
        "chunks": chunk_count,
        "policy_rules": rule_count,
        "agreements": sum(1 for d in docs if d.agreement_terms),
    }
    logger.info("ingestion complete: %s", json.dumps(summary))
    return summary


def _iso(value: str | None) -> str | None:
    dt = parse_workbook_timestamp(value)
    return dt.isoformat() if dt else None


def _nullable_bool(value) -> int | None:
    if value is None:
        return None
    return int(bool(value))
