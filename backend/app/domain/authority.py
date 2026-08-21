"""Source-authority resolution layer.

Implements the precedence model required by the assessment:

1. An ACTIVE signed customer agreement overrides the generic default only
   for the subjects it actually addresses (support SLA, cancellation fee,
   failed-pickup credit terms).
2. Otherwise, the applicable CURRENT authoritative document for that domain
   governs (support policy v3 / cancellation SOP v4 / product ops guide).
3. The deprecated support policy (v2) is excluded from this resolution
   entirely -- it is looked up dynamically via `source_documents.status`,
   never by filename, so nothing here special-cases "v2".
4. Historical ticket resolutions are never consulted here at all; see
   app/domain/conflicts.py for how they are surfaced as context-only.

Domain is resolved first (support SLA vs. cancellation vs. credit vs.
product capability); precedence is then applied *within* that domain. We
never rank documents on a single global numeric scale across domains.
"""
from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel

from app.domain.models import Account


class Citation(BaseModel):
    source_file: str
    section: str | None = None
    authority_category: str
    status: str
    detail: str | None = None


class SupportSLAResolution(BaseModel):
    plan: str
    severity: Literal["P1", "P2", "P3"]
    target_value: float
    target_unit: str
    is_24x7: bool
    source: Literal["agreement_override", "default_policy", "unresolved"]
    citation: Citation | None = None


class CancellationRuleResolution(BaseModel):
    no_fee_window_minutes: float | None
    fee_after_window_inr: float | None
    fee_waived_override: bool
    override_note: str | None = None
    source: Literal["agreement_override", "default_sop"]
    citations: list[Citation]


class ServiceCreditRuleResolution(BaseModel):
    threshold_hours: float | None
    threshold_strict: str
    amount_fixed: float | None
    amount_is_default: bool
    default_cap_inr: float | None
    default_pct_of_fee: float | None
    manager_approval_threshold_inr: float | None
    monthly_aggregate_cap_inr: float | None
    source: Literal["agreement_override", "default_sop"]
    citations: list[Citation]


def _agreement_row(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM agreement_terms WHERE account_id = ?", (account_id,)).fetchone()


def _current_file_for_doc_type(conn: sqlite3.Connection, doc_type: str) -> str | None:
    row = conn.execute(
        "SELECT file_name FROM source_documents WHERE doc_type = ? AND status = 'current' LIMIT 1",
        (doc_type,),
    ).fetchone()
    return row["file_name"] if row else None


def _chunk_citation(conn: sqlite3.Connection, file_name: str, section_like: str, detail: str) -> Citation | None:
    row = conn.execute(
        "SELECT * FROM document_chunks WHERE file_name = ? AND section LIKE ? LIMIT 1",
        (file_name, f"%{section_like}%"),
    ).fetchone()
    doc = conn.execute("SELECT * FROM source_documents WHERE file_name = ?", (file_name,)).fetchone()
    if not doc:
        return None
    return Citation(
        source_file=file_name,
        section=row["section"] if row else None,
        authority_category=doc["authority_category"],
        status=doc["status"],
        detail=detail,
    )


def resolve_support_sla(conn: sqlite3.Connection, account: Account, severity: Literal["P1", "P2", "P3"]) -> SupportSLAResolution:
    agreement = _agreement_row(conn, account.account_id)
    sev_lower = severity.lower()

    if agreement is not None and agreement[f"support_{sev_lower}_value"] is not None:
        citation = _chunk_citation(conn, agreement["source_file"], "Support terms", f"{severity} support target override")
        return SupportSLAResolution(
            plan=account.plan, severity=severity,
            target_value=agreement[f"support_{sev_lower}_value"],
            target_unit=agreement[f"support_{sev_lower}_unit"],
            is_24x7=bool(agreement["support_p1_24x7"]) if severity == "P1" else False,
            source="agreement_override", citation=citation,
        )

    current_file = _current_file_for_doc_type(conn, "support_policy")
    if current_file is None:
        return SupportSLAResolution(
            plan=account.plan, severity=severity, target_value=0, target_unit="unknown",
            is_24x7=False, source="unresolved", citation=None,
        )
    row = conn.execute(
        """SELECT pr.* FROM policy_rules pr
           WHERE pr.source_file = ? AND pr.domain = 'support_sla' AND pr.plan = ? AND pr.severity = ?""",
        (current_file, account.plan, severity),
    ).fetchone()
    if row is None:
        return SupportSLAResolution(
            plan=account.plan, severity=severity, target_value=0, target_unit="unknown",
            is_24x7=False, source="unresolved", citation=None,
        )
    citation = _chunk_citation(conn, current_file, "Default first-response targets", f"Default {account.plan} {severity} target")
    return SupportSLAResolution(
        plan=account.plan, severity=severity, target_value=row["target_value"], target_unit=row["target_unit"],
        is_24x7=bool(row["is_24x7"]), source="default_policy", citation=citation,
    )


def resolve_cancellation_rule(conn: sqlite3.Connection, account: Account) -> CancellationRuleResolution:
    current_file = _current_file_for_doc_type(conn, "cancellation_sop")
    citations = []
    no_fee_window = fee_after = None
    if current_file:
        for row in conn.execute(
            "SELECT * FROM policy_rules WHERE source_file = ? AND domain = 'cancellation'", (current_file,),
        ):
            if row["notes"] == "no_fee_window_minutes":
                no_fee_window = row["target_value"]
            elif row["notes"] == "cancellation_fee_after_window":
                fee_after = row["target_value"]
        c = _chunk_citation(conn, current_file, "Order cancellation", "Default cancellation fee rule")
        if c:
            citations.append(c)

    agreement = _agreement_row(conn, account.account_id)
    if agreement is not None and agreement["cancellation_fee_waived"]:
        c = _chunk_citation(conn, agreement["source_file"], "cancellation", "Agreement cancellation-fee waiver")
        if c:
            citations.append(c)
        return CancellationRuleResolution(
            no_fee_window_minutes=no_fee_window, fee_after_window_inr=fee_after,
            fee_waived_override=True, override_note=agreement["cancellation_notes"],
            source="agreement_override", citations=citations,
        )

    return CancellationRuleResolution(
        no_fee_window_minutes=no_fee_window, fee_after_window_inr=fee_after,
        fee_waived_override=False,
        override_note=agreement["cancellation_notes"] if agreement is not None else None,
        source="default_sop", citations=citations,
    )


def resolve_service_credit_rule(conn: sqlite3.Connection, account: Account) -> ServiceCreditRuleResolution:
    current_file = _current_file_for_doc_type(conn, "cancellation_sop")
    citations = []
    threshold_hours = cap_inr = pct = manager_threshold = None
    if current_file:
        for row in conn.execute(
            "SELECT * FROM policy_rules WHERE source_file = ? AND domain = 'service_credit'", (current_file,),
        ):
            if row["notes"] == "default_credit_delay_threshold_hours":
                threshold_hours = row["target_value"]
            elif row["notes"] == "default_credit_cap_inr":
                cap_inr = row["target_value"]
            elif row["notes"] == "default_credit_percent":
                pct = row["target_value"]
            elif row["notes"] == "manager_approval_threshold_inr":
                manager_threshold = row["target_value"]
        c = _chunk_citation(conn, current_file, "Failed-pickup service credits", "Default failed-pickup credit rule")
        if c:
            citations.append(c)

    agreement = _agreement_row(conn, account.account_id)
    if agreement is not None and not bool(agreement["credit_amount_is_default"]):
        c = _chunk_citation(conn, agreement["source_file"], "credits", "Agreement failed-pickup credit override")
        if c:
            citations.append(c)
        return ServiceCreditRuleResolution(
            threshold_hours=agreement["credit_threshold_hours"],
            threshold_strict=agreement["credit_threshold_strict"] or "strictly_greater_than",
            amount_fixed=agreement["credit_amount_fixed"], amount_is_default=False,
            default_cap_inr=cap_inr, default_pct_of_fee=pct,
            manager_approval_threshold_inr=manager_threshold,
            monthly_aggregate_cap_inr=agreement["credit_monthly_cap"],
            source="agreement_override", citations=citations,
        )

    return ServiceCreditRuleResolution(
        threshold_hours=threshold_hours, threshold_strict="strictly_greater_than",
        amount_fixed=None, amount_is_default=True,
        default_cap_inr=cap_inr, default_pct_of_fee=pct,
        manager_approval_threshold_inr=manager_threshold,
        monthly_aggregate_cap_inr=agreement["credit_monthly_cap"] if agreement is not None else None,
        source="default_sop", citations=citations,
    )
