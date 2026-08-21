from __future__ import annotations

from app.domain.snapshot import get_snapshot


def test_snapshot_loaded_from_workbook_not_system_clock(conn):
    snap = get_snapshot()
    assert snap.isoformat() == "2026-08-16T11:00:00+05:30"
    assert snap.weekday() == 6  # Sunday


def test_all_source_documents_ingested(conn):
    rows = conn.execute("SELECT file_name, doc_type, status FROM source_documents ORDER BY file_name").fetchall()
    files = {r["file_name"]: r for r in rows}
    assert len(files) == 6
    assert files["01_Support_Policy_v3_CURRENT.pdf"]["status"] == "current"
    assert files["02_Support_Policy_v2_DEPRECATED.pdf"]["status"] == "historical_only"
    assert files["03_Cancellation_and_Service_Credit_SOP_v4.pdf"]["status"] == "current"
    assert files["04_Product_Operations_Guide_and_Known_Issues.pdf"]["status"] == "current"
    assert files["05_Northstar_Logistics_Enterprise_Agreement.pdf"]["status"] == "current"
    assert files["06_LumenWorks_Service_Agreement.pdf"]["status"] == "current"


def test_document_chunks_carry_authority_metadata(conn):
    row = conn.execute(
        "SELECT * FROM document_chunks WHERE file_name = '02_Support_Policy_v2_DEPRECATED.pdf' LIMIT 1"
    ).fetchone()
    assert row["status"] == "historical_only"
    assert row["authority_category"] == "historical_deprecated"

    row2 = conn.execute(
        "SELECT * FROM document_chunks WHERE file_name = '05_Northstar_Logistics_Enterprise_Agreement.pdf' LIMIT 1"
    ).fetchone()
    assert row2["customer_scope"] == "ACCT-001"
    assert row2["authority_category"] == "active_customer_agreement"


def test_accounts_orders_tickets_row_counts(conn):
    assert conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] == 4
    assert conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 6
    assert conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 7


def test_agreement_terms_extracted_dynamically(conn):
    northstar = conn.execute("SELECT * FROM agreement_terms WHERE account_id = 'ACCT-001'").fetchone()
    assert northstar["support_p1_value"] == 15.0
    assert northstar["support_p1_unit"] == "minutes"
    assert bool(northstar["support_p1_24x7"]) is True
    assert bool(northstar["cancellation_fee_waived"]) is True
    assert northstar["credit_monthly_cap"] == 5000.0

    lumenworks = conn.execute("SELECT * FROM agreement_terms WHERE account_id = 'ACCT-002'").fetchone()
    assert lumenworks["support_p1_value"] == 2.0
    assert lumenworks["support_p1_unit"] == "business_hours"
    assert bool(lumenworks["no_weekend_afterhours"]) is True
    assert lumenworks["credit_threshold_hours"] == 4.0
    assert lumenworks["credit_amount_fixed"] == 300.0
    assert bool(lumenworks["credit_amount_is_default"]) is False


def test_ingestion_is_idempotent(conn):
    from pathlib import Path

    from app.ingestion.loader import run_ingestion

    data_dir = Path(__file__).resolve().parent.parent / "data"
    summary1 = run_ingestion(conn, data_dir)
    summary2 = run_ingestion(conn, data_dir)
    assert summary1 == summary2
    assert conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] == 4
