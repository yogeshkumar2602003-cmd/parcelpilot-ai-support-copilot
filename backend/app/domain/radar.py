"""Issue Radar: internal-only proactive issue detection (bonus feature).

Deliberately deterministic and LLM-free so the radar page keeps working
even when ANTHROPIC_API_KEY is not configured. Severity/known-issue
matching uses lightweight keyword overlap against the actual ingested
policy/known-issue text (see _keywords_from_text) rather than hardcoded
per-ticket-ID rules, so it generalizes to any ticket in the dataset.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

from pydantic import BaseModel

from app.domain.authority import resolve_support_sla
from app.domain.calculations import evaluate_ticket_sla
from app.domain.conflicts import detect_historical_conflict
from app.domain.models import Account, Ticket
from app.domain.repositories import Repositories
from app.domain.snapshot import get_snapshot

STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "are", "be",
    "this", "that", "any", "no", "not", "may", "will", "when", "then", "at", "by", "as", "it",
    "customer", "customers", "another", "event", "causing", "immediate", "material", "business",
}


def _keywords_from_text(text: str) -> set[str]:
    tokens = re.findall(r"[a-z]{4,}", text.lower())
    return {t for t in tokens if t not in STOPWORDS}


def _ticket_text(ticket: Ticket) -> str:
    return f"{ticket.subject} {ticket.description}"


class RadarCard(BaseModel):
    kind: str
    title: str
    detail: str
    ticket_ids: list[str] = []
    account_ids: list[str] = []
    severity_estimate: str | None = None


def build_issue_radar(conn: sqlite3.Connection) -> dict:
    from app.auth.principal import DEMO_USERS

    admin = DEMO_USERS["u_admin"]
    repos = Repositories.build(conn)
    now = get_snapshot()

    open_tickets = repos.tickets.search(admin, status="open")
    all_tickets = repos.tickets.search(admin)
    accounts_by_id: dict[str, Account] = {a.account_id: a for a in repos.accounts.list_all(admin)}

    # --- P1-candidate signature, derived from the actual current P1 definition text ---
    p1_row = conn.execute(
        """SELECT dc.text FROM document_chunks dc
           JOIN source_documents sd ON sd.file_name = dc.file_name
           WHERE sd.doc_type = 'support_policy' AND sd.status = 'current' AND dc.section LIKE '%Severity%'
           LIMIT 1"""
    ).fetchone()
    p1_sentence = ""
    if p1_row:
        # Severity items are bullet-separated ("<bullet> P1 - ... <bullet> P2 - ...").
        # Split on any non-alphanumeric bullet glyph rather than assuming a
        # specific character, since PDF bullet glyphs vary by exporter.
        parts = re.split(r"[•●▪‣*]", p1_row["text"])
        p1_part = next((p for p in parts if p.strip().startswith("P1")), "")
        p1_sentence = p1_part or p1_row["text"]
    p1_keywords = _keywords_from_text(p1_sentence)

    known_issue_rows = conn.execute(
        """SELECT dc.section, dc.text FROM document_chunks dc
           JOIN source_documents sd ON sd.file_name = dc.file_name
           WHERE sd.doc_type = 'product_ops_guide' AND sd.status = 'current' AND dc.section LIKE 'KI-%'"""
    ).fetchall()
    ki_keywords_raw = {row["section"]: _keywords_from_text(row["text"]) for row in known_issue_rows}
    # Keep only each known issue's DISTINCTIVE terms (not shared with another
    # known issue, and not part of the generic P1 signature) so two known
    # issues that both incidentally mention "shipment creation" don't both
    # fire on a ticket about neither of them.
    term_doc_counts: dict[str, int] = defaultdict(int)
    for kws in ki_keywords_raw.values():
        for k in kws:
            term_doc_counts[k] += 1
    ki_keywords = {
        ki: {k for k in kws if term_doc_counts[k] == 1 and k not in p1_keywords}
        for ki, kws in ki_keywords_raw.items()
    }

    cards: list[RadarCard] = []

    # 1. P1 candidates among open tickets
    p1_candidates = []
    for t in open_tickets:
        overlap = _keywords_from_text(_ticket_text(t)) & p1_keywords
        if len(overlap) >= 2:
            p1_candidates.append((t, overlap))
    if p1_candidates:
        cards.append(RadarCard(
            kind="p1_candidate",
            title=f"{len(p1_candidates)} open ticket(s) resemble a P1/critical incident",
            detail="Matched against the current Support Policy v3 P1 definition (outage / security / no-workaround "
                   "language). Verify severity before acting.",
            ticket_ids=[t.ticket_id for t, _ in p1_candidates],
            account_ids=list({t.account_id for t, _ in p1_candidates}),
            severity_estimate="P1",
        ))

    # 2. Known-issue matches
    ki_matches: dict[str, list[Ticket]] = defaultdict(list)
    for t in open_tickets:
        t_kw = _keywords_from_text(_ticket_text(t))
        for ki, kws in ki_keywords.items():
            if len(t_kw & kws) >= 2:
                ki_matches[ki].append(t)
    for ki, tickets in ki_matches.items():
        distinct_accounts = {t.account_id for t in tickets}
        cross_account_note = (
            f"Affects {len(distinct_accounts)} distinct account(s): {', '.join(sorted(distinct_accounts))}."
            if len(distinct_accounts) > 1
            else f"Currently observed for a single account ({next(iter(distinct_accounts))}) only -- do not "
                 "report this as a multi-customer incident without more evidence."
        )
        cards.append(RadarCard(
            kind="known_issue_match",
            title=f"{len(tickets)} open ticket(s) match known issue {ki}",
            detail=cross_account_note,
            ticket_ids=[t.ticket_id for t in tickets],
            account_ids=sorted(distinct_accounts),
        ))

    # 3. SLA approaching/exceeding (potential, unconfirmed -- no first-response timestamp in dataset)
    sla_flags = []
    for t in open_tickets:
        account = accounts_by_id.get(t.account_id)
        if not account:
            continue
        severity = "P1" if any(t.ticket_id == ct.ticket_id for ct, _ in p1_candidates) else "P3"
        sla = resolve_support_sla(conn, account, severity)
        ev = evaluate_ticket_sla(t, sla, now)
        if ev.potential_breach:
            sla_flags.append((t, ev))
    if sla_flags:
        cards.append(RadarCard(
            kind="sla_risk",
            title=f"{len(sla_flags)} open ticket(s): potential breach / response verification required",
            detail="Ticket age exceeds the estimated first-response target for its likely severity. The dataset "
                   "has no first-response timestamp, so this is a POTENTIAL breach requiring human verification, "
                   "not a confirmed one.",
            ticket_ids=[t.ticket_id for t, _ in sla_flags],
            account_ids=list({t.account_id for t, _ in sla_flags}),
        ))

    # 4. Unresolved historical conflicts
    conflict_tickets = []
    for t in all_tickets:
        account = accounts_by_id.get(t.account_id)
        if not account:
            continue
        conflict = detect_historical_conflict(t, account, conn)
        if conflict:
            conflict_tickets.append((t, conflict))
    if conflict_tickets:
        cards.append(RadarCard(
            kind="historical_conflict",
            title=f"{len(conflict_tickets)} historical ticket(s) conflict with current authoritative sources",
            detail="These historical resolutions should never be reused as current guidance.",
            ticket_ids=[t.ticket_id for t, _ in conflict_tickets],
            account_ids=list({t.account_id for t, _ in conflict_tickets}),
        ))

    # 5. Recurring keyword clusters among open tickets (excluding already-classified known-issue matches)
    clusters: dict[str, list[Ticket]] = defaultdict(list)
    for t in open_tickets:
        for kw in _keywords_from_text(_ticket_text(t)):
            clusters[kw].append(t)
    recurring = {kw: ts for kw, ts in clusters.items() if len(ts) >= 2 and len({t.ticket_id for t in ts}) >= 2}
    for kw, ts in list(recurring.items())[:3]:
        distinct = {t.account_id for t in ts}
        cards.append(RadarCard(
            kind="recurring_pattern",
            title=f'Recurring term "{kw}" across {len(ts)} open ticket(s)',
            detail=(f"Spans {len(distinct)} account(s)." if len(distinct) > 1 else "Single account so far."),
            ticket_ids=[t.ticket_id for t in ts], account_ids=sorted(distinct),
        ))

    return {
        "generated_at_snapshot": now.isoformat(),
        "open_ticket_count": len(open_tickets),
        "cards": [c.model_dump(mode="json") for c in cards],
    }
