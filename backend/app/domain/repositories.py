"""Access-controlled data repositories.

THIS is the security boundary for structured data. Every function here
takes the authenticated `Principal` and enforces account scoping directly
against the SQL query / result set. The LLM tool layer calls these
functions but cannot bypass them -- there is no code path from a tool
argument straight to raw SQL.

A customer principal:
  * can only ever receive rows for principal.account_id
  * receives a field-allowlisted (public) Ticket view, never internal
    fields such as assigned_to
  * gets AuthorizationError (not a 404, not a leak) for out-of-scope IDs

An internal principal (support/operations/admin) can read across accounts.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.domain.models import Account, Order, Principal, Ticket, TicketPublic


class AuthorizationError(Exception):
    """Raised when a principal requests a record outside their access
    scope. Callers must not leak whether the record exists."""


class NotFoundError(Exception):
    pass


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        account_id=row["account_id"], account_name=row["account_name"], plan=row["plan"],
        status=row["status"], csm=row["csm"], contract_file=row["contract_file"],
        premium_support=bool(row["premium_support"]), notes=row["notes"],
    )


def _row_to_order(row: sqlite3.Row) -> Order:
    return Order(
        order_id=row["order_id"], account_id=row["account_id"], carrier=row["carrier"],
        status=row["status"], booked_at=row["booked_at"], pickup_window_start=row["pickup_window_start"],
        pickup_window_end=row["pickup_window_end"], pickup_actual_at=row["pickup_actual_at"],
        shipment_fee_inr=row["shipment_fee_inr"],
        carrier_fault=None if row["carrier_fault"] is None else bool(row["carrier_fault"]),
        customer_fault=None if row["customer_fault"] is None else bool(row["customer_fault"]),
        cancellation_requested_at=row["cancellation_requested_at"], notes=row["notes"],
    )


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        ticket_id=row["ticket_id"], account_id=row["account_id"], created_at=row["created_at"],
        status=row["status"], subject=row["subject"], description=row["description"],
        channel=row["channel"], assigned_to=row["assigned_to"],
        last_customer_message_at=row["last_customer_message_at"],
        historical_resolution=row["historical_resolution"],
    )


class AccountRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, principal: Principal, account_id: str) -> Account:
        if not principal.can_access_account(account_id):
            raise AuthorizationError(f"principal {principal.user_id} may not access account {account_id}")
        row = self.conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        if not row:
            raise NotFoundError(f"account {account_id} not found")
        return _row_to_account(row)

    def get_own(self, principal: Principal) -> Account | None:
        if not principal.account_id:
            return None
        return self.get(principal, principal.account_id)

    def list_all(self, principal: Principal) -> list[Account]:
        if not principal.is_internal():
            raise AuthorizationError("only internal principals may list all accounts")
        rows = self.conn.execute("SELECT * FROM accounts ORDER BY account_id").fetchall()
        return [_row_to_account(r) for r in rows]


class OrderRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, principal: Principal, order_id: str) -> Order:
        row = self.conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if not row:
            raise NotFoundError(f"order {order_id} not found")
        if not principal.can_access_account(row["account_id"]):
            # Do not reveal that the order exists for another account.
            raise AuthorizationError(f"principal {principal.user_id} may not access order {order_id}")
        return _row_to_order(row)

    def search(
        self, principal: Principal, account_id: str | None = None, status: str | None = None,
    ) -> list[Order]:
        if principal.is_internal():
            scope_account_id = account_id  # may be None (search all) or any account
        else:
            if account_id and account_id != principal.account_id:
                # A customer naming another account never widens their scope.
                raise AuthorizationError("customers may only search their own account's orders")
            scope_account_id = principal.account_id

        query = "SELECT * FROM orders WHERE 1=1"
        params: list = []
        if scope_account_id:
            query += " AND account_id = ?"
            params.append(scope_account_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY order_id"
        rows = self.conn.execute(query, params).fetchall()
        return [_row_to_order(r) for r in rows]


class TicketRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, principal: Principal, ticket_id: str) -> Ticket:
        row = self.conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        if not row:
            raise NotFoundError(f"ticket {ticket_id} not found")
        if not principal.can_access_account(row["account_id"]):
            raise AuthorizationError(f"principal {principal.user_id} may not access ticket {ticket_id}")
        return _row_to_ticket(row)

    def get_scoped(self, principal: Principal, ticket_id: str) -> Ticket | TicketPublic:
        """Returns the field-allowlisted view for customers, full ticket for
        internal principals."""
        t = self.get(principal, ticket_id)
        if principal.is_internal():
            return t
        return TicketPublic.from_ticket(t)

    def search(
        self, principal: Principal, account_id: str | None = None, status: str | None = None,
    ) -> list[Ticket]:
        if principal.is_internal():
            scope_account_id = account_id
        else:
            if account_id and account_id != principal.account_id:
                raise AuthorizationError("customers may only search their own account's tickets")
            scope_account_id = principal.account_id

        query = "SELECT * FROM tickets WHERE 1=1"
        params: list = []
        if scope_account_id:
            query += " AND account_id = ?"
            params.append(scope_account_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = self.conn.execute(query, params).fetchall()
        return [_row_to_ticket(r) for r in rows]

    def search_scoped(self, principal: Principal, **kwargs) -> list[Ticket] | list[TicketPublic]:
        tickets = self.search(principal, **kwargs)
        if principal.is_internal():
            return tickets
        return [TicketPublic.from_ticket(t) for t in tickets]

    def update_status_and_assignment(
        self, ticket_id: str, *, status: str | None = None, assigned_to: str | None = None,
    ) -> None:
        """Low-level mutation used ONLY by the confirmed-action executor
        (app/actions/pending_actions.py), never called directly by tools."""
        fields, params = [], []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if assigned_to is not None:
            fields.append("assigned_to = ?")
            params.append(assigned_to)
        if not fields:
            return
        params.append(ticket_id)
        self.conn.execute(f"UPDATE tickets SET {', '.join(fields)} WHERE ticket_id = ?", params)
        self.conn.commit()


@dataclass
class Repositories:
    accounts: AccountRepository
    orders: OrderRepository
    tickets: TicketRepository

    @classmethod
    def build(cls, conn: sqlite3.Connection) -> "Repositories":
        return cls(AccountRepository(conn), OrderRepository(conn), TicketRepository(conn))
