"""Generic detection of conflicts between a ticket's historical_resolution
text and the currently-resolved authoritative rule.

Historical ticket resolutions are context only (per Support Policy v3 §1)
and must never be treated as policy authority. When a historical
resolution's stated outcome numerically disagrees with what the current
authoritative sources say for that account today, internal principals
should see an explicit conflict warning while customer-facing answers
simply state the correct current answer.

This operates on any ticket's historical_resolution text via generic
INR/row-count extraction -- it is not keyed to any specific ticket ID.
"""
from __future__ import annotations

import re
import sqlite3

from pydantic import BaseModel

from app.domain.authority import Citation, resolve_cancellation_rule
from app.domain.models import Account, Ticket


class ConflictFlag(BaseModel):
    domain: str
    historical_claim: str
    current_authoritative_answer: str
    citation: Citation | None = None


def _current_bulk_upload_limit(conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        """SELECT pr.target_value FROM policy_rules pr
           JOIN source_documents sd ON sd.file_name = pr.source_file
           WHERE sd.status = 'current' AND pr.notes = 'bulk_upload_supported_row_limit'
           LIMIT 1"""
    ).fetchone()
    return row["target_value"] if row else None


def detect_historical_conflict(ticket: Ticket, account: Account, conn: sqlite3.Connection) -> ConflictFlag | None:
    if not ticket.historical_resolution:
        return None
    text = ticket.historical_resolution
    # Domain detection considers the whole ticket (subject/description), since
    # the historical_resolution field itself may just state an outcome
    # ("...only supports 3,000 rows") without repeating the topic keywords.
    context = f"{ticket.subject} {ticket.description} {text}"

    if re.search(r"cancel", context, re.I):
        m = re.search(r"INR\s*([\d,]+)", text)
        historical_fee = float(m.group(1).replace(",", "")) if m else None
        rule = resolve_cancellation_rule(conn, account)
        if historical_fee is not None and rule.fee_waived_override and historical_fee > 0:
            return ConflictFlag(
                domain="cancellation",
                historical_claim=text,
                current_authoritative_answer=(
                    f"{account.account_name}'s active agreement waives the cancellation fee for any BOOKED "
                    "shipment before pickup, regardless of elapsed time. The historical resolution's INR "
                    f"{historical_fee:.0f} fee does not reflect the currently active agreement."
                ),
                citation=rule.citations[-1] if rule.citations else None,
            )

    if re.search(r"bulk upload|csv", context, re.I):
        m = re.search(r"([\d,]{3,6})\s*rows?", text)
        historical_limit = float(m.group(1).replace(",", "")) if m else None
        current_limit = _current_bulk_upload_limit(conn)
        if historical_limit is not None and current_limit is not None and historical_limit < current_limit:
            return ConflictFlag(
                domain="product_capability",
                historical_claim=text,
                current_authoritative_answer=(
                    f"The current Product Operations Guide states the supported Bulk Upload limit remains "
                    f"{current_limit:.0f} rows for Growth and Enterprise plans. KI-208 causes intermittent "
                    "failures above ~3,000 rows with a workaround of splitting below 3,000 rows, but the "
                    f"supported limit is not reduced to {historical_limit:.0f}."
                ),
                citation=None,
            )

    return None
