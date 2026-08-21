"""Tool definitions and the access-controlled tool executor.

Every tool handler receives the authenticated `Principal` bound at
construction time -- it is never taken from the LLM's tool-call
arguments -- so the LLM cannot widen its own access by, say, passing a
different account_id. Handlers call straight into the repository /
authority / calculations layers, which independently re-enforce scoping.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from app.actions.pending_actions import ActionAuthorizationError, propose_action
from app.agent.evidence import ConfidenceTracker, evidence_label, make_evidence
from app.domain.authority import resolve_cancellation_rule, resolve_service_credit_rule, resolve_support_sla
from app.domain.calculations import evaluate_cancellation, evaluate_service_credit, evaluate_ticket_sla
from app.domain.conflicts import detect_historical_conflict
from app.domain.models import Evidence, Principal
from app.domain.repositories import AuthorizationError, NotFoundError, Repositories
from app.domain.snapshot import get_snapshot
from app.retrieval.retriever import get_index

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": (
            "Search the ParcelPilot document knowledge base: support policy, cancellation & service-credit SOP, "
            "product operations guide / known issues, and customer agreements. Automatically scoped to what the "
            "current user is authorized to see. Returns ranked text chunks with authority metadata (current vs. "
            "historical, section, customer scope)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query."},
                "doc_type": {
                    "type": "string",
                    "enum": ["support_policy", "cancellation_sop", "product_ops_guide", "customer_agreement"],
                    "description": "Optional: restrict to one document type/domain.",
                },
                "include_historical": {
                    "type": "boolean",
                    "description": (
                        "Only internal users may set this true, to explicitly retrieve deprecated/historical "
                        "policy text (e.g. when asked about historical policy or a version conflict). Never set "
                        "this for a normal current-request answer."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_account",
        "description": "Fetch a single account by account_id (plan, status, CSM, contract file).",
        "input_schema": {"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]},
    },
    {
        "name": "get_order",
        "description": "Fetch a single order by order_id (status, carrier, timestamps, fault flags).",
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    },
    {
        "name": "get_ticket",
        "description": "Fetch a single support ticket by ticket_id.",
        "input_schema": {"type": "object", "properties": {"ticket_id": {"type": "string"}}, "required": ["ticket_id"]},
    },
    {
        "name": "search_orders",
        "description": "Search orders, optionally filtered by account_id and/or status. Results are always scoped to the caller's authorized accounts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "status": {"type": "string", "enum": ["DRAFT", "BOOKED", "PICKED_UP", "DELIVERED", "CANCELLED"]},
            },
        },
    },
    {
        "name": "search_tickets",
        "description": (
            "Search support tickets, optionally filtered by account_id and/or status. Results are always "
            "scoped to the caller's authorized accounts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}, "status": {"type": "string"}},
        },
    },
    {
        "name": "calculate_cancellation",
        "description": (
            "Deterministically evaluate whether an order can be cancelled and what fee (if any) applies, using "
            "the order's actual booking/pickup/cancellation-request timestamps and the resolved cancellation rule "
            "(active agreement override or current SOP default) for its account."
        ),
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    },
    {
        "name": "calculate_service_credit",
        "description": (
            "Deterministically evaluate failed-pickup service-credit eligibility and amount for an order, using "
            "pickup-window/actual-pickup timestamps, carrier_fault/customer_fault flags, and the resolved credit "
            "rule (agreement override or current SOP default) for its account."
        ),
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    },
    {
        "name": "calculate_ticket_sla",
        "description": (
            "Compute a ticket's age at the dataset snapshot and compare it against the resolved first-response "
            "SLA target for the given severity (P1/P2/P3) and the ticket's account. You must first determine the "
            "correct severity yourself by reading the severity definitions (search_documents) against the "
            "ticket's description -- this tool does not classify severity for you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
            },
            "required": ["ticket_id", "severity"],
        },
    },
    {
        "name": "propose_action",
        "description": (
            "PHASE 1 ONLY: prepare a state-changing action (escalation, ticket update, or follow-up task) for "
            "explicit human confirmation. This NEVER executes anything by itself -- it only creates a pending "
            "proposal that the user must confirm in the UI. Do not tell the user the action is done; tell them "
            "you have prepared it and are awaiting their confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "enum": ["create_escalation", "update_ticket", "create_followup_task"]},
                "reason": {"type": "string", "description": "Why this action is being proposed, for the audit log."},
                "ticket_id": {"type": "string"},
                "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
                "new_status": {"type": "string"},
                "assigned_to": {"type": "string"},
                "description": {"type": "string"},
                "due_at": {"type": "string"},
            },
            "required": ["action_type", "reason"],
        },
    },
]

HUMAN_LABELS = {
    "get_account": lambda i: f"Looking up account {i.get('account_id', '')}",
    "get_order": lambda i: f"Looking up order {i.get('order_id', '')}",
    "get_ticket": lambda i: f"Looking up ticket {i.get('ticket_id', '')}",
    "search_orders": lambda i: "Searching orders",
    "search_tickets": lambda i: "Searching tickets",
    "calculate_cancellation": lambda i: f"Calculating cancellation eligibility for {i.get('order_id', '')}",
    "calculate_service_credit": lambda i: f"Calculating service credit for {i.get('order_id', '')}",
    "calculate_ticket_sla": lambda i: f"Checking {i.get('severity', '')} SLA target for {i.get('ticket_id', '')}",
    "propose_action": lambda i: f"Preparing {str(i.get('action_type', 'action')).replace('_', ' ')}",
}


def _search_documents_label(i: dict[str, Any]) -> str:
    doc_type = i.get("doc_type")
    labels = {
        "customer_agreement": "Reading customer agreement",
        "cancellation_sop": "Checking current cancellation SOP",
        "support_policy": "Checking support policy",
        "product_ops_guide": "Checking product operations guide & known issues",
    }
    return labels.get(doc_type, f'Searching documents for "{str(i.get("query", ""))[:50]}"')


HUMAN_LABELS["search_documents"] = _search_documents_label


class ToolExecutionError(Exception):
    pass


@dataclass
class ToolCallRecord:
    tool_name: str
    tool_input: dict[str, Any]
    label: str
    result_summary: str
    ok: bool


class ToolExecutor:
    def __init__(self, conn: sqlite3.Connection, principal: Principal):
        self.conn = conn
        self.principal = principal
        self.repos = Repositories.build(conn)
        self.confidence = ConfidenceTracker()
        self.trace: list[ToolCallRecord] = []
        self.evidence: list[Evidence] = []
        self.pending_action = None
        self.conflicts: list[str] = []

    def human_label(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        fn = HUMAN_LABELS.get(tool_name)
        return fn(tool_input) if fn else f"Running {tool_name}"

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        label = self.human_label(tool_name, tool_input)
        try:
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                result = {"error": "unknown_tool", "message": f"No such tool: {tool_name}"}
                self.confidence.note_unsupported(f"tool '{tool_name}'")
            else:
                result = handler(tool_input)
            self.trace.append(ToolCallRecord(tool_name, tool_input, label, _summarize(result), "error" not in result))
            return result
        except AuthorizationError:
            self.confidence.note_authorization_denied(tool_name)
            result = {"error": "unauthorized", "message": "You are not authorized to access this record."}
            self.trace.append(ToolCallRecord(tool_name, tool_input, label, "unauthorized", False))
            return result
        except NotFoundError as e:
            self.confidence.note_not_found(tool_name)
            result = {"error": "not_found", "message": str(e)}
            self.trace.append(ToolCallRecord(tool_name, tool_input, label, "not found", False))
            return result
        except ActionAuthorizationError as e:
            result = {"error": "unauthorized", "message": str(e)}
            self.trace.append(ToolCallRecord(tool_name, tool_input, label, "unauthorized", False))
            return result

    # -- document search --------------------------------------------------
    def _tool_search_documents(self, inp: dict[str, Any]) -> dict[str, Any]:
        include_historical = bool(inp.get("include_historical")) and self.principal.is_internal()
        results = get_index().search(
            self.principal, inp.get("query", ""), doc_type=inp.get("doc_type"),
            include_historical=include_historical, top_k=5,
        )
        if not results:
            return {"results": [], "message": "No accessible documents matched this query."}
        out = []
        for r in results:
            self.confidence.note_evidence_used()
            self.evidence.append(make_evidence(r.file_name, r.section, r.authority_category, r.status))
            out.append({
                "label": evidence_label(r.file_name, r.section),
                "file_name": r.file_name, "doc_type": r.doc_type, "status": r.status,
                "authority_category": r.authority_category, "effective_date": r.effective_date,
                "updated_date": r.updated_date, "customer_scope": r.customer_scope,
                "section": r.section, "page": r.page, "text": r.text, "relevance_score": round(r.score, 3),
            })
        return {"results": out}

    # -- structured lookups -------------------------------------------------
    def _tool_get_account(self, inp: dict[str, Any]) -> dict[str, Any]:
        acc = self.repos.accounts.get(self.principal, inp["account_id"])
        self.confidence.note_evidence_used()
        self.evidence.append(Evidence(
            label=f"Assessment workbook / accounts / {acc.account_id}", source_file="ParcelPilot_Assessment_Data.xlsx",
            section="accounts", authority_category="operational_fact", status="current",
        ))
        return {"account": acc.model_dump(mode="json")}

    def _tool_get_order(self, inp: dict[str, Any]) -> dict[str, Any]:
        order = self.repos.orders.get(self.principal, inp["order_id"])
        self.confidence.note_evidence_used()
        self.evidence.append(Evidence(
            label=f"Assessment workbook / orders / {order.order_id}", source_file="ParcelPilot_Assessment_Data.xlsx",
            section="orders", authority_category="operational_fact", status="current",
        ))
        return {"order": order.model_dump(mode="json")}

    def _tool_get_ticket(self, inp: dict[str, Any]) -> dict[str, Any]:
        ticket = self.repos.tickets.get_scoped(self.principal, inp["ticket_id"])
        self.confidence.note_evidence_used()
        self.evidence.append(Evidence(
            label=f"Assessment workbook / tickets / {ticket.ticket_id}", source_file="ParcelPilot_Assessment_Data.xlsx",
            section="tickets", authority_category="operational_fact", status="current",
        ))
        out: dict[str, Any] = {"ticket": ticket.model_dump(mode="json")}
        if self.principal.is_internal():
            full_ticket = self.repos.tickets.get(self.principal, inp["ticket_id"])
            account = self.repos.accounts.get(self.principal, full_ticket.account_id)
            conflict = detect_historical_conflict(full_ticket, account, self.conn)
            if conflict:
                out["historical_conflict"] = conflict.model_dump(mode="json")
                self.conflicts.append(
                    f"Historical ticket {full_ticket.ticket_id} conflicts with the current authoritative "
                    f"source ({conflict.domain}). Current answer used; historical resolution was NOT followed."
                )
        return out

    def _tool_search_orders(self, inp: dict[str, Any]) -> dict[str, Any]:
        orders = self.repos.orders.search(self.principal, account_id=inp.get("account_id"), status=inp.get("status"))
        self.confidence.note_evidence_used()
        return {"orders": [o.model_dump(mode="json") for o in orders], "count": len(orders)}

    def _tool_search_tickets(self, inp: dict[str, Any]) -> dict[str, Any]:
        tickets = self.repos.tickets.search_scoped(self.principal, account_id=inp.get("account_id"), status=inp.get("status"))
        self.confidence.note_evidence_used()
        return {"tickets": [t.model_dump(mode="json") for t in tickets], "count": len(tickets)}

    # -- calculations ---------------------------------------------------
    def _tool_calculate_cancellation(self, inp: dict[str, Any]) -> dict[str, Any]:
        order = self.repos.orders.get(self.principal, inp["order_id"])
        account = self.repos.accounts.get(self.principal, order.account_id)
        rule = resolve_cancellation_rule(self.conn, account)
        result = evaluate_cancellation(order, rule, get_snapshot())
        self.confidence.note_missing_facts(result.missing_facts, f"cancellation evaluation of {order.order_id}")
        self.confidence.note_evidence_used()
        for c in rule.citations:
            self.evidence.append(make_evidence(c.source_file, c.section, c.authority_category, c.status, c.detail))
        return {"evaluation": result.model_dump(mode="json"), "rule_citations": [c.model_dump(mode="json") for c in rule.citations]}

    def _tool_calculate_service_credit(self, inp: dict[str, Any]) -> dict[str, Any]:
        order = self.repos.orders.get(self.principal, inp["order_id"])
        account = self.repos.accounts.get(self.principal, order.account_id)
        rule = resolve_service_credit_rule(self.conn, account)
        result = evaluate_service_credit(order, rule, get_snapshot())
        self.confidence.note_missing_facts(result.missing_facts, f"service-credit evaluation of {order.order_id}")
        if result.monthly_cap_note:
            self.confidence.note_unverifiable_cap(result.monthly_cap_note)
        self.confidence.note_evidence_used()
        for c in rule.citations:
            self.evidence.append(make_evidence(c.source_file, c.section, c.authority_category, c.status, c.detail))
        return {"evaluation": result.model_dump(mode="json"), "rule_citations": [c.model_dump(mode="json") for c in rule.citations]}

    def _tool_calculate_ticket_sla(self, inp: dict[str, Any]) -> dict[str, Any]:
        ticket = self.repos.tickets.get(self.principal, inp["ticket_id"])
        account = self.repos.accounts.get(self.principal, ticket.account_id)
        sla = resolve_support_sla(self.conn, account, inp["severity"])
        result = evaluate_ticket_sla(ticket, sla, get_snapshot())
        if sla.source == "unresolved":
            self.confidence.note_unresolved_rule(f"SLA for {ticket.ticket_id}")
        if result.is_estimate:
            self.confidence.note_estimate(f"SLA target for {ticket.ticket_id}")
        if result.potential_breach:
            self.confidence.note_unconfirmed_breach(f"Ticket {ticket.ticket_id}")
        self.confidence.note_evidence_used()
        citation = sla.citation.model_dump(mode="json") if sla.citation else None
        if sla.citation:
            self.evidence.append(make_evidence(
                sla.citation.source_file, sla.citation.section, sla.citation.authority_category,
                sla.citation.status, sla.citation.detail,
            ))
        return {"evaluation": result.model_dump(mode="json"), "sla_source": sla.source, "rule_citation": citation}

    # -- state-changing action (phase 1: propose only) --------------------
    def _tool_propose_action(self, inp: dict[str, Any]) -> dict[str, Any]:
        action_type = inp["action_type"]
        payload: dict[str, Any] = {}
        if action_type == "create_escalation":
            payload = {"ticket_id": inp.get("ticket_id"), "severity": inp.get("severity")}
        elif action_type == "update_ticket":
            payload = {"ticket_id": inp.get("ticket_id"), "status": inp.get("new_status"), "assigned_to": inp.get("assigned_to")}
        elif action_type == "create_followup_task":
            payload = {"ticket_id": inp.get("ticket_id"), "description": inp.get("description"), "due_at": inp.get("due_at")}

        account_id = None
        ticket_id = inp.get("ticket_id")
        if ticket_id:
            try:
                ticket = self.repos.tickets.get(self.principal, ticket_id)
                account_id = ticket.account_id
            except (AuthorizationError, NotFoundError):
                pass

        action = propose_action(
            self.conn, self.principal, action_type, payload, inp.get("reason", ""), account_id=account_id,
        )
        self.pending_action = action
        self.confidence.note_evidence_used()
        return {
            "pending_action": action.model_dump(mode="json"),
            "message": "Action prepared. It will NOT execute until the user explicitly confirms it in the UI.",
        }


def _summarize(result: dict[str, Any]) -> str:
    if "error" in result:
        return str(result.get("message", result["error"]))
    if "results" in result:
        return f"{len(result['results'])} document result(s)"
    if "orders" in result:
        return f"{result.get('count', len(result['orders']))} order(s)"
    if "tickets" in result:
        return f"{result.get('count', len(result['tickets']))} ticket(s)"
    if "evaluation" in result:
        return "calculation complete"
    if "pending_action" in result:
        return f"proposed {result['pending_action']['action_type']}"
    return "ok"
