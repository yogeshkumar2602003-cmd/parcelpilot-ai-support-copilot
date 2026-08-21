"""Core Pydantic v2 domain models shared across the backend."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["customer", "support", "operations", "admin"]


class Principal(BaseModel):
    """The authenticated actor making a request.

    This is the single source of truth for authorization decisions. It is
    constructed server-side from a trusted demo-user registry (see
    app/auth/principal.py) -- it is NEVER built from client-supplied role or
    account_id fields, so a client cannot escalate privilege by tampering
    with request payloads.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str
    display_name: str
    role: Role
    account_id: str | None = None
    permissions: frozenset[str] = Field(default_factory=frozenset)

    def is_internal(self) -> bool:
        return self.role in ("support", "operations", "admin")

    def can_access_account(self, account_id: str) -> bool:
        if self.is_internal():
            return True
        return self.account_id == account_id


class Account(BaseModel):
    account_id: str
    account_name: str
    plan: Literal["Enterprise", "Growth", "Standard"]
    status: str
    csm: str | None = None
    contract_file: str | None = None
    premium_support: bool = False
    notes: str | None = None


class Order(BaseModel):
    order_id: str
    account_id: str
    carrier: str
    status: Literal["DRAFT", "BOOKED", "PICKED_UP", "DELIVERED", "CANCELLED"]
    booked_at: datetime | None = None
    pickup_window_start: datetime | None = None
    pickup_window_end: datetime | None = None
    pickup_actual_at: datetime | None = None
    shipment_fee_inr: float | None = None
    carrier_fault: bool | None = None
    customer_fault: bool | None = None
    cancellation_requested_at: datetime | None = None
    notes: str | None = None


class Ticket(BaseModel):
    ticket_id: str
    account_id: str
    created_at: datetime
    status: str
    subject: str
    description: str
    channel: str | None = None
    assigned_to: str | None = None
    last_customer_message_at: datetime | None = None
    historical_resolution: str | None = None
    """Historical resolution text. CONTEXT ONLY -- never treat as policy
    authority. See app/domain/authority.py."""


class TicketPublic(BaseModel):
    """Field-allowlisted ticket view for customer-role principals.

    Excludes internal-only fields such as assigned_to and
    historical_resolution.
    """

    ticket_id: str
    account_id: str
    created_at: datetime
    status: str
    subject: str
    description: str
    channel: str | None = None
    last_customer_message_at: datetime | None = None

    @classmethod
    def from_ticket(cls, t: Ticket) -> "TicketPublic":
        return cls(**{k: getattr(t, k) for k in cls.model_fields})


class DocumentChunk(BaseModel):
    """A retrievable unit of a source document with full authority metadata."""

    chunk_id: str
    file_name: str
    doc_type: Literal[
        "support_policy",
        "cancellation_sop",
        "product_ops_guide",
        "customer_agreement",
    ]
    status: Literal["current", "deprecated", "historical_only"]
    authority_category: Literal[
        "current_support_policy",
        "current_cancellation_sop",
        "current_product_ops",
        "active_customer_agreement",
        "historical_deprecated",
    ]
    effective_date: str | None = None
    updated_date: str | None = None
    customer_scope: str | None = None  # account_id, or None for generic docs
    agreement_term_start: str | None = None
    agreement_term_end: str | None = None
    section: str | None = None
    page: int = 1
    text: str


class PendingAction(BaseModel):
    action_id: str
    action_type: Literal["create_escalation", "update_ticket", "create_followup_task"]
    payload: dict[str, Any]
    reason: str
    requested_by_user_id: str
    requested_by_role: Role
    account_id: str | None = None
    status: Literal["pending", "confirmed", "cancelled", "executed", "expired"] = "pending"
    created_at: datetime
    decided_at: datetime | None = None
    decided_by_user_id: str | None = None
    result: dict[str, Any] | None = None


class Evidence(BaseModel):
    label: str
    source_file: str
    section: str | None = None
    page: int | None = None
    authority_category: str
    status: str
    detail: str | None = None


class AgentAnswer(BaseModel):
    answer_markdown: str
    why: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: Literal["High", "Medium", "Low"]
    uncertainty_reason: str | None = None
    conflict_warning: str | None = None
    pending_action: PendingAction | None = None
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
