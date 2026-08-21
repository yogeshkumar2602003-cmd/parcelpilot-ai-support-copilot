"""Shared evidence/citation formatting and confidence scoring.

Confidence is computed HERE, deterministically, from structured signals
that tool handlers attach to their results -- never from an LLM-reported
probability. See docs/ARCHITECTURE.md "Trust & Reliability UX".
"""
from __future__ import annotations

import re

from app.domain.models import Evidence

DOC_TITLES = {
    "01_Support_Policy_v3_CURRENT.pdf": "Support Policy v3",
    "02_Support_Policy_v2_DEPRECATED.pdf": "Support Policy v2 (deprecated)",
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf": "Cancellation & Service Credit SOP v4",
    "04_Product_Operations_Guide_and_Known_Issues.pdf": "Product Operations Guide",
    "05_Northstar_Logistics_Enterprise_Agreement.pdf": "Northstar Logistics Enterprise Agreement",
    "06_LumenWorks_Service_Agreement.pdf": "LumenWorks Service Agreement",
}


def doc_title(file_name: str) -> str:
    return DOC_TITLES.get(file_name, file_name)


def short_section(section: str | None) -> str | None:
    if not section:
        return None
    m = re.match(r"^(\d+)\.", section.strip())
    if m:
        return f"§{m.group(1)}"
    if section.upper().startswith("KI-"):
        return section.split()[0]
    return section[:24]


def evidence_label(file_name: str, section: str | None) -> str:
    sec = short_section(section)
    return f"{doc_title(file_name)} {sec}" if sec else doc_title(file_name)


def make_evidence(file_name: str, section: str | None, authority_category: str, status: str,
                   detail: str | None = None) -> Evidence:
    return Evidence(
        label=evidence_label(file_name, section), source_file=file_name, section=section,
        page=None, authority_category=authority_category, status=status, detail=detail,
    )


class ConfidenceTracker:
    """Accumulates signals across a turn's tool calls, worst-signal-wins."""

    def __init__(self) -> None:
        self.low_reasons: list[str] = []
        self.medium_reasons: list[str] = []
        self.had_any_evidence = False

    def note_missing_facts(self, facts: list[str], context: str) -> None:
        if facts:
            self.low_reasons.append(f"Missing required facts for {context}: {', '.join(facts)}.")

    def note_unresolved_rule(self, context: str) -> None:
        self.low_reasons.append(f"No authoritative rule could be resolved for {context}.")

    def note_authorization_denied(self, context: str) -> None:
        self.low_reasons.append(f"Access denied for {context}.")

    def note_not_found(self, context: str) -> None:
        self.low_reasons.append(f"{context} was not found in the dataset.")

    def note_unsupported(self, context: str) -> None:
        self.low_reasons.append(f"{context} is outside this system's supported capabilities.")

    def note_estimate(self, context: str) -> None:
        self.medium_reasons.append(f"{context} relies on a demo business-hours estimate, not an authoritative calendar.")

    def note_unconfirmed_breach(self, context: str) -> None:
        self.medium_reasons.append(f"{context}: possible SLA breach cannot be confirmed without a first-response timestamp.")

    def note_stale_status_risk(self, context: str) -> None:
        self.medium_reasons.append(context)

    def note_unverifiable_cap(self, context: str) -> None:
        self.medium_reasons.append(context)

    def note_evidence_used(self) -> None:
        self.had_any_evidence = True

    def resolve(self) -> tuple[str, str | None]:
        if self.low_reasons:
            return "Low", " ".join(self.low_reasons)
        if not self.had_any_evidence:
            return "Low", "No document or dataset evidence was gathered for this answer."
        if self.medium_reasons:
            return "Medium", " ".join(self.medium_reasons)
        return "High", None
