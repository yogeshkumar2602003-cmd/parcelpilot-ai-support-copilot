from __future__ import annotations

from app.auth.principal import DEMO_USERS
from app.retrieval.retriever import get_index

ADMIN = DEMO_USERS["u_admin"]
NORTHSTAR_CUSTOMER = DEMO_USERS["u_cust_northstar"]
LUMENWORKS_CUSTOMER = DEMO_USERS["u_cust_lumenworks"]


def test_deprecated_policy_excluded_by_default(conn):
    idx = get_index()
    results = idx.search(ADMIN, "Growth P1 support SLA business hours", top_k=10)
    assert all(r.status != "historical_only" for r in results)


def test_internal_can_explicitly_retrieve_historical_with_flag(conn):
    idx = get_index()
    results = idx.search(ADMIN, "Growth P1 support SLA business hours", include_historical=True, top_k=10)
    assert any(r.status == "historical_only" for r in results)


def test_customer_cannot_retrieve_historical_even_with_flag(conn):
    idx = get_index()
    results = idx.search(NORTHSTAR_CUSTOMER, "deprecated policy", include_historical=True, top_k=10)
    assert all(r.status != "historical_only" for r in results)


def test_customer_cannot_retrieve_other_customers_agreement(conn):
    idx = get_index()
    results = idx.search(NORTHSTAR_CUSTOMER, "LumenWorks service agreement failed pickup credit", top_k=10)
    assert all(r.customer_scope != "ACCT-002" for r in results)


def test_customer_can_retrieve_own_agreement(conn):
    idx = get_index()
    results = idx.search(NORTHSTAR_CUSTOMER, "Northstar cancellation fee waiver", top_k=10)
    assert any(r.customer_scope == "ACCT-001" for r in results)


def test_customer_can_retrieve_generic_current_policy(conn):
    idx = get_index()
    results = idx.search(LUMENWORKS_CUSTOMER, "cancellation fee 30 minutes", top_k=10)
    assert any(r.doc_type == "cancellation_sop" for r in results)


def test_ki_208_and_ki_211_are_retrievable(conn):
    idx = get_index()
    r1 = idx.search(ADMIN, "bulk upload CSV failure large rows", doc_type="product_ops_guide", top_k=5)
    assert any("KI-208" in (r.section or "") for r in r1)
    r2 = idx.search(ADMIN, "SwiftShip still BOOKED after pickup webhook delay", doc_type="product_ops_guide", top_k=5)
    assert any("KI-211" in (r.section or "") for r in r2)
