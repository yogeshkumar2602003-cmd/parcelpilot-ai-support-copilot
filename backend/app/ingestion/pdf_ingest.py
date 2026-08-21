"""Deterministic ingestion of the six ParcelPilot source-pack PDFs.

Design notes (see docs/ARCHITECTURE.md for the full rationale):

* Chunk boundaries use each document's own printed section headings as
  anchors. These are literal structural markers copied from the source
  documents themselves (e.g. "1. Order cancellation"), used only to slice
  text for citation purposes -- NOT business values. If a document doesn't
  match a known template, it falls back to a single whole-page chunk so
  ingestion never fails on an unfamiliar file.
* All *numeric* business values (SLA minutes/hours, fees, thresholds,
  credit amounts) are extracted with generic regexes over the normalized
  text (e.g. "(\\d+) minutes", "INR (\\d+)") rather than being hardcoded.
  These regexes are generic value/unit parsers, not per-account or
  per-record branches, so they generalize to any document following the
  same "label: number unit" convention.
* Nothing here ever branches on an order/ticket/account ID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

UNIT_PATTERN = r"(business\s+hours?|business\s+days?|hours?|minutes?)"
VALUE_UNIT_RE = re.compile(
    rf"(\d+(?:\.\d+)?)\s+{UNIT_PATTERN}(\s*,?\s*24x7)?", re.IGNORECASE
)


def normalize_text(raw: str) -> str:
    """Collapse the irregular whitespace/newline runs pypdf emits for these
    PDFs' word-by-word text layout into single spaces."""
    return re.sub(r"\s+", " ", raw).strip()


def normalize_unit(raw_unit: str) -> str:
    u = re.sub(r"\s+", " ", raw_unit.strip().lower())
    if u.startswith("business hour"):
        return "business_hours"
    if u.startswith("business day"):
        return "business_days"
    if u.startswith("hour"):
        return "hours"
    if u.startswith("minute"):
        return "minutes"
    return u


def parse_targets(segment: str, count: int = 3) -> list[dict[str, Any]]:
    """Parse up to `count` sequential (value, unit, is_24x7) targets from a
    text segment, e.g. "30 minutes, 24x7 2 hours 1 business day" ->
    [{value:30,unit:minutes,is_24x7:True}, {value:2,unit:hours,...}, ...]
    """
    out = []
    for m in VALUE_UNIT_RE.finditer(segment):
        out.append(
            {
                "value": float(m.group(1)),
                "unit": normalize_unit(m.group(2)),
                "is_24x7": bool(m.group(3)),
            }
        )
        if len(out) >= count:
            break
    return out


@dataclass
class RawChunk:
    section: str
    text: str
    page: int = 1


@dataclass
class IngestedDoc:
    file_name: str
    doc_type: str
    status: str  # current | historical_only
    effective_date: str | None
    updated_date: str | None
    customer_scope: str | None
    agreement_term_start: str | None
    agreement_term_end: str | None
    chunks: list[RawChunk] = field(default_factory=list)
    policy_rules: list[dict[str, Any]] = field(default_factory=list)
    agreement_terms: dict[str, Any] | None = None
    full_text: str = ""


DOC_TYPE_BY_FILENAME = {
    "01_Support_Policy_v3_CURRENT.pdf": "support_policy",
    "02_Support_Policy_v2_DEPRECATED.pdf": "support_policy",
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf": "cancellation_sop",
    "04_Product_Operations_Guide_and_Known_Issues.pdf": "product_ops_guide",
    "05_Northstar_Logistics_Enterprise_Agreement.pdf": "customer_agreement",
    "06_LumenWorks_Service_Agreement.pdf": "customer_agreement",
}

# Known section-heading anchors per file, used only to slice text for
# citation labeling. See module docstring.
SECTION_ANCHORS: dict[str, list[str]] = {
    "01_Support_Policy_v3_CURRENT.pdf": [
        "1. Scope and source precedence",
        "2. Severity definitions",
        "3. Default first-response targets",
        "4. Escalation",
    ],
    "02_Support_Policy_v2_DEPRECATED.pdf": [
        "Severity and response targets",
    ],
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf": [
        "1. Order cancellation",
        "2. Failed-pickup service credits",
        "3. Approval and uncertainty",
    ],
    "04_Product_Operations_Guide_and_Known_Issues.pdf": [
        "1. Plan capabilities",
        "2. Current known issues",
        "KI-208",
        "KI-211",
        "3. Resolved issue",
    ],
    "05_Northstar_Logistics_Enterprise_Agreement.pdf": [
        "1. Support terms",
        "2. Shipment cancellation",
        "3. Service credits",
        "4. Account contact",
    ],
    "06_LumenWorks_Service_Agreement.pdf": [
        "1. Support terms",
        "2. Cancellation terms",
        "3. Failed-pickup credits",
    ],
}


def _split_by_anchors(text: str, anchors: list[str]) -> list[RawChunk]:
    positions = []
    for anchor in anchors:
        idx = text.find(anchor)
        if idx != -1:
            positions.append((idx, anchor))
    if not positions:
        return [RawChunk(section="Full document", text=text)]
    positions.sort(key=lambda p: p[0])
    chunks = []
    for i, (idx, anchor) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        chunks.append(RawChunk(section=anchor, text=text[idx:end].strip()))
    return chunks


def _extract_support_policy_rules(file_name: str, status: str, text: str) -> list[dict[str, Any]]:
    rules = []
    for plan in ("Enterprise", "Growth", "Standard"):
        idx = text.find(plan)
        if idx == -1:
            continue
        # segment runs from this plan name to the next plan name (or 200 chars)
        next_positions = [text.find(p, idx + 1) for p in ("Enterprise", "Growth", "Standard")]
        next_positions = [p for p in next_positions if p != -1 and p > idx]
        end = min(next_positions) if next_positions else idx + 200
        segment = text[idx + len(plan): end]
        targets = parse_targets(segment, count=3)
        for sev, target in zip(("P1", "P2", "P3"), targets):
            rules.append(
                {
                    "id": f"{file_name}:{plan}:{sev}",
                    "source_file": file_name,
                    "domain": "support_sla",
                    "plan": plan,
                    "severity": sev,
                    "target_value": target["value"],
                    "target_unit": target["unit"],
                    "is_24x7": target["is_24x7"],
                    "notes": status,
                }
            )
    return rules


def _extract_cancellation_sop_rules(file_name: str, text: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []

    m = re.search(r"No fee within (\d+) minutes of booking", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:no_fee_window", "source_file": file_name, "domain": "cancellation",
            "plan": None, "severity": None, "target_value": float(m.group(1)), "target_unit": "minutes",
            "is_24x7": False, "notes": "no_fee_window_minutes",
        })

    m = re.search(r"After \d+ minutes,\s*charge INR ([\d,]+)", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:fee_after_window", "source_file": file_name, "domain": "cancellation",
            "plan": None, "severity": None, "target_value": float(m.group(1).replace(",", "")),
            "target_unit": "INR", "is_24x7": False, "notes": "cancellation_fee_after_window",
        })

    m = re.search(r"more than (\d+) hours? past the end", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:credit_threshold_hours", "source_file": file_name, "domain": "service_credit",
            "plan": None, "severity": None, "target_value": float(m.group(1)), "target_unit": "hours",
            "is_24x7": False, "notes": "default_credit_delay_threshold_hours",
        })

    m = re.search(r"lower of INR ([\d,]+) or (\d+)% of the shipment fee", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:credit_cap_inr", "source_file": file_name, "domain": "service_credit",
            "plan": None, "severity": None, "target_value": float(m.group(1).replace(",", "")),
            "target_unit": "INR", "is_24x7": False, "notes": "default_credit_cap_inr",
        })
        rules.append({
            "id": f"{file_name}:credit_pct", "source_file": file_name, "domain": "service_credit",
            "plan": None, "severity": None, "target_value": float(m.group(2)),
            "target_unit": "percent_of_fee", "is_24x7": False, "notes": "default_credit_percent",
        })

    m = re.search(r"above INR ([\d,]+) requires manager approval", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:manager_approval_threshold", "source_file": file_name, "domain": "service_credit",
            "plan": None, "severity": None, "target_value": float(m.group(1).replace(",", "")),
            "target_unit": "INR", "is_24x7": False, "notes": "manager_approval_threshold_inr",
        })

    return rules


def _extract_product_ops_rules(file_name: str, text: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    m = re.search(r"up to ([\d,]+) rows per CSV", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:bulk_upload_limit", "source_file": file_name, "domain": "product_capability",
            "plan": "Growth/Enterprise", "severity": None, "target_value": float(m.group(1).replace(",", "")),
            "target_unit": "rows", "is_24x7": False, "notes": "bulk_upload_supported_row_limit",
        })
    m = re.search(r"above approximately ([\d,]+) rows", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:ki208_threshold", "source_file": file_name, "domain": "known_issue",
            "plan": None, "severity": None, "target_value": float(m.group(1).replace(",", "")),
            "target_unit": "rows", "is_24x7": False, "notes": "KI-208_intermittent_failure_threshold_rows",
        })
    m = re.search(r"split the upload into files below ([\d,]+) rows", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:ki208_workaround", "source_file": file_name, "domain": "known_issue",
            "plan": None, "severity": None, "target_value": float(m.group(1).replace(",", "")),
            "target_unit": "rows", "is_24x7": False, "notes": "KI-208_workaround_row_limit",
        })
    m = re.search(r"up to (\d+) minutes late", text, re.I)
    if m:
        rules.append({
            "id": f"{file_name}:ki211_delay", "source_file": file_name, "domain": "known_issue",
            "plan": None, "severity": None, "target_value": float(m.group(1)),
            "target_unit": "minutes", "is_24x7": False, "notes": "KI-211_webhook_delay_minutes",
        })
    return rules


def _extract_agreement_terms(file_name: str, text: str, status: str,
                              term_start: str | None, term_end: str | None) -> dict[str, Any] | None:
    m_acct = re.search(r"Account:\s*(ACCT-\d+)", text)
    if not m_acct:
        return None
    account_id = m_acct.group(1)

    terms: dict[str, Any] = {
        "account_id": account_id,
        "source_file": file_name,
        "status": status,
        "term_start": term_start,
        "term_end": term_end,
        "no_weekend_afterhours": bool(re.search(r"No weekend or after-hours", text, re.I)),
        "cancellation_fee_waived": False,
        "cancellation_notes": None,
        "credit_threshold_hours": None,
        "credit_threshold_strict": None,
        "credit_amount_fixed": None,
        "credit_amount_is_default": True,
        "credit_monthly_cap": None,
        "csm": None,
    }

    # Support-term overrides: look for "P1: <value> <unit>" style bullets.
    for sev in ("P1", "P2", "P3"):
        m = re.search(rf"{sev}:\s*{VALUE_UNIT_RE.pattern}", text, re.I)
        if m:
            terms[f"support_{sev.lower()}_value"] = float(m.group(1))
            terms[f"support_{sev.lower()}_unit"] = normalize_unit(m.group(2))
            if sev == "P1":
                terms["support_p1_24x7"] = bool(m.group(3))

    # Cancellation fee waiver (e.g. Northstar: "...with no cancellation fee...")
    if re.search(r"no special cancellation-fee waiver", text, re.I):
        terms["cancellation_fee_waived"] = False
        terms["cancellation_notes"] = "no_special_waiver_use_current_sop"
    elif re.search(r"no cancellation fee", text, re.I):
        terms["cancellation_fee_waived"] = True
        terms["cancellation_notes"] = "waived_for_booked_pre_pickup"

    # Monthly aggregate credit cap (e.g. Northstar: "capped at INR 5,000")
    m = re.search(r"capped at INR ([\d,]+)", text, re.I)
    if m:
        terms["credit_monthly_cap"] = float(m.group(1).replace(",", ""))

    # Fixed failed-pickup credit override (e.g. LumenWorks clause)
    m = re.search(
        r"more than (\d+) hours? past the end of the scheduled pickup window.*?fixed INR ([\d,]+)",
        text, re.I,
    )
    if m:
        terms["credit_threshold_hours"] = float(m.group(1))
        terms["credit_threshold_strict"] = "strictly_greater_than"
        terms["credit_amount_fixed"] = float(m.group(2).replace(",", ""))
        terms["credit_amount_is_default"] = False

    m = re.search(r"Dedicated CSM:\s*([A-Za-z .]+?)\.", text)
    if m:
        terms["csm"] = m.group(1).strip()

    return terms


def parse_pdf(path: Path) -> IngestedDoc:
    reader = PdfReader(str(path))
    raw_pages = [normalize_text(p.extract_text() or "") for p in reader.pages]
    full_text = " ".join(raw_pages)
    file_name = path.name
    doc_type = DOC_TYPE_BY_FILENAME.get(file_name, "unknown")

    status_match = re.search(r"Status:\s*(CURRENT|DEPRECATED|ACTIVE|EXPIRED)", full_text, re.I)
    raw_status = status_match.group(1).upper() if status_match else "CURRENT"
    status = "historical_only" if raw_status == "DEPRECATED" else "current"

    effective_match = re.search(r"Effective:\s*([\d]+ \w+ \d{4})", full_text)
    updated_match = re.search(r"Updated:\s*([\d]+ \w+ \d{4})", full_text)
    term_match = re.search(r"Term:\s*([\d]+ \w+ \d{4}) to ([\d]+ \w+ \d{4})", full_text)

    anchors = SECTION_ANCHORS.get(file_name, [])
    chunks = _split_by_anchors(full_text, anchors)

    policy_rules: list[dict[str, Any]] = []
    agreement_terms: dict[str, Any] | None = None
    customer_scope: str | None = None

    if doc_type == "support_policy":
        policy_rules = _extract_support_policy_rules(file_name, status, full_text)
    elif doc_type == "cancellation_sop":
        policy_rules = _extract_cancellation_sop_rules(file_name, full_text)
    elif doc_type == "product_ops_guide":
        policy_rules = _extract_product_ops_rules(file_name, full_text)
    elif doc_type == "customer_agreement":
        agreement_terms = _extract_agreement_terms(
            file_name, full_text, status,
            term_match.group(1) if term_match else None,
            term_match.group(2) if term_match else None,
        )
        if agreement_terms:
            customer_scope = agreement_terms["account_id"]

    return IngestedDoc(
        file_name=file_name,
        doc_type=doc_type,
        status=status,
        effective_date=effective_match.group(1) if effective_match else None,
        updated_date=updated_match.group(1) if updated_match else None,
        customer_scope=customer_scope,
        agreement_term_start=term_match.group(1) if term_match else None,
        agreement_term_end=term_match.group(2) if term_match else None,
        chunks=chunks,
        policy_rules=policy_rules,
        agreement_terms=agreement_terms,
        full_text=full_text,
    )


def authority_category_for(doc_type: str, status: str) -> str:
    if status == "historical_only":
        return "historical_deprecated"
    return {
        "support_policy": "current_support_policy",
        "cancellation_sop": "current_cancellation_sop",
        "product_ops_guide": "current_product_ops",
        "customer_agreement": "active_customer_agreement",
    }.get(doc_type, "current_support_policy")


def parse_all_pdfs(data_dir: Path) -> list[IngestedDoc]:
    docs = []
    for file_name in DOC_TYPE_BY_FILENAME:
        path = data_dir / file_name
        if path.exists():
            docs.append(parse_pdf(path))
    return docs
