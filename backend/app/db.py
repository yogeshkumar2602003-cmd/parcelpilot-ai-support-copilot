"""SQLite connection management and schema. Ingestion is deterministic and
idempotent: re-running startup always reflects the current source pack.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    account_name TEXT NOT NULL,
    plan TEXT NOT NULL,
    status TEXT NOT NULL,
    csm TEXT,
    contract_file TEXT,
    premium_support INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    carrier TEXT,
    status TEXT NOT NULL,
    booked_at TEXT,
    pickup_window_start TEXT,
    pickup_window_end TEXT,
    pickup_actual_at TEXT,
    shipment_fee_inr REAL,
    carrier_fault INTEGER,
    customer_fault INTEGER,
    cancellation_requested_at TEXT,
    notes TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    subject TEXT,
    description TEXT,
    channel TEXT,
    assigned_to TEXT,
    last_customer_message_at TEXT,
    historical_resolution TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS source_documents (
    file_name TEXT PRIMARY KEY,
    doc_type TEXT NOT NULL,
    status TEXT NOT NULL,
    authority_category TEXT NOT NULL,
    effective_date TEXT,
    updated_date TEXT,
    customer_scope TEXT
);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    status TEXT NOT NULL,
    authority_category TEXT NOT NULL,
    effective_date TEXT,
    updated_date TEXT,
    customer_scope TEXT,
    agreement_term_start TEXT,
    agreement_term_end TEXT,
    section TEXT,
    page INTEGER,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_rules (
    id TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    domain TEXT NOT NULL,
    plan TEXT,
    severity TEXT,
    target_value REAL,
    target_unit TEXT,
    is_24x7 INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS agreement_terms (
    account_id TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    status TEXT,
    term_start TEXT,
    term_end TEXT,
    support_p1_value REAL,
    support_p1_unit TEXT,
    support_p1_24x7 INTEGER,
    support_p2_value REAL,
    support_p2_unit TEXT,
    support_p3_value REAL,
    support_p3_unit TEXT,
    no_weekend_afterhours INTEGER NOT NULL DEFAULT 0,
    cancellation_fee_waived INTEGER NOT NULL DEFAULT 0,
    cancellation_notes TEXT,
    credit_threshold_hours REAL,
    credit_threshold_strict TEXT,
    credit_amount_fixed REAL,
    credit_amount_is_default INTEGER NOT NULL DEFAULT 1,
    credit_monthly_cap REAL,
    csm TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS pending_actions (
    action_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    requested_by_user_id TEXT NOT NULL,
    requested_by_role TEXT NOT NULL,
    account_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by_user_id TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS escalations (
    id TEXT PRIMARY KEY,
    ticket_id TEXT,
    account_id TEXT,
    severity TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    created_by_user_id TEXT,
    action_id TEXT
);

CREATE TABLE IF NOT EXISTS followup_tasks (
    id TEXT PRIMARY KEY,
    ticket_id TEXT,
    account_id TEXT,
    description TEXT,
    due_at TEXT,
    created_at TEXT NOT NULL,
    created_by_user_id TEXT,
    action_id TEXT
);

CREATE TABLE IF NOT EXISTS ticket_updates (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL,
    created_by_user_id TEXT,
    action_id TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    request_id TEXT,
    user_id TEXT,
    role TEXT,
    account_id TEXT,
    event_type TEXT NOT NULL,
    detail_json TEXT
);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def wipe_operational_tables(conn: sqlite3.Connection) -> None:
    """Idempotent re-ingestion: clear derived/source-of-truth tables that are
    fully rebuilt from the source pack on every startup. Mutable
    demo-generated tables (pending_actions, escalations, followup_tasks,
    ticket_updates, audit_log) are intentionally left untouched.
    """
    # Children before parents: orders/tickets/agreement_terms reference accounts.
    for table in (
        "orders", "tickets", "agreement_terms",
        "document_chunks", "policy_rules", "source_documents",
        "accounts",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


_singleton_conn: sqlite3.Connection | None = None


def get_singleton_connection() -> sqlite3.Connection:
    global _singleton_conn
    if _singleton_conn is None:
        _singleton_conn = get_connection()
        init_schema(_singleton_conn)
    return _singleton_conn


@contextmanager
def connection_scope():
    conn = get_singleton_connection()
    try:
        yield conn
    finally:
        pass
