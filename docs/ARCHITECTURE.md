# Architecture Note

## 1. Overview

```
┌─────────────────────┐        ┌──────────────────────────────────────────────┐
│  React + Vite (TS)  │  HTTP  │  FastAPI (Python 3.12)                        │
│  Chat UI, Radar,     │◄──────►│  - api/ (chat, actions, misc)                │
│  Audit Log           │        │  - agent/ (orchestrator, tools, evidence)     │
│  built & served by   │        │  - domain/ (repositories, authority,          │
│  FastAPI in prod     │        │    calculations, conflicts, business calendar)│
└─────────────────────┘        │  - retrieval/ (BM25 over document_chunks)     │
                                │  - actions/ (two-phase pending actions)       │
                                │  - ingestion/ (PDF + workbook loaders)        │
                                └──────────────┬─────────────────────────────────┘
                                               │
                                        SQLite (single file)
                                accounts, orders, tickets, document_chunks,
                                policy_rules, agreement_terms, source_documents,
                                pending_actions, escalations, followup_tasks,
                                ticket_updates, audit_log
```

Everything is one deployable process: FastAPI serves the JSON API and, in production, the built React static
files, so the whole app is one container with one health check.

## 2. Agent design

A hand-rolled, typed tool-use loop (`backend/app/agent/orchestrator.py`) over the Anthropic Messages API, rather
than a general agent framework. Rationale: the tool set is small and fixed (10 tools), and the properties that
matter most for this assessment — access control, confirmation-gated mutation, deterministic confidence — are
much easier to audit and unit-test in ~150 lines of explicit loop code than inside a framework's abstractions.

The loop:
1. Builds a system prompt encoding the source-authority rules, the dataset snapshot as "now", and role-specific
   access-control instructions (`build_system_prompt`).
2. Calls the LLM with the running message list + tool schemas.
3. If the model requests tool calls, each is dispatched through `ToolExecutor`, which is constructed with the
   authenticated `Principal` and re-enforces authorization independently of anything the LLM passed as
   arguments. Tool results (JSON) are appended back into the message list.
4. Repeats until the model returns plain text (final answer) or `PARCELPILOT_MAX_TOOL_DEPTH` (default 8) is hit,
   at which point the loop stops gracefully with a Low-confidence "please rephrase or escalate" answer instead of
   spinning forever.
5. Confidence, evidence citations, and any historical-conflict warning are computed **after** the loop from
   structured signals the tools recorded — never asked of the LLM as a self-reported number. See §6.

The **model id is never hardcoded**; it's read from `ANTHROPIC_MODEL` (`backend/app/config.py`), so a retired
model can be swapped via environment variable alone.

## 3. Tool design

Four tool *categories*, ten concrete tools (`backend/app/agent/tools.py`):

| Category | Tools | Notes |
|---|---|---|
| Document search | `search_documents` | BM25 over `document_chunks`, principal-scoped (see §5) |
| Structured lookup | `get_account`, `get_order`, `get_ticket`, `search_orders`, `search_tickets` | Go through `Repositories`, which enforce account scoping in SQL/result filtering, not in the tool layer |
| Calculation | `calculate_cancellation`, `calculate_service_credit`, `calculate_ticket_sla` | Pure Python over already-fetched facts; the LLM never does the arithmetic itself |
| State-changing (phase 1 only) | `propose_action` | Creates a `PendingAction` row with `status=pending`; **cannot execute anything** |

Every tool handler is a method on `ToolExecutor`, constructed once per request with `(conn, principal)`. The
principal is closed over in Python, not passed as a tool argument the model could spoof. Handlers translate
domain exceptions (`AuthorizationError`, `NotFoundError`) into safe, non-leaking JSON error results and record a
`ConfidenceTracker` signal so the eventual confidence score reflects what actually happened.

Severity classification (P1/P2/P3) is **not** a deterministic tool — the model must read the retrieved severity
definitions and the ticket text itself and pass its judgment as the `severity` argument to
`calculate_ticket_sla`. This was a deliberate choice: hand-coding severity classification risks silently
overfitting the assessment's exact wording, whereas the arithmetic/lookup steps that follow (SLA target
resolution, age comparison) are fully deterministic and unit-tested.

## 4. Document handling

- **Ingestion** (`backend/app/ingestion/pdf_ingest.py`) extracts text with `pypdf`, normalizes the exporter's
  irregular whitespace, and slices each document into cited sections using each document's own printed
  headings as anchors (e.g. `"1. Order cancellation"`). These anchors are literal structural markers copied from
  the source documents — used only to label chunks for citation, never to encode a business answer. Any document
  that doesn't match a known heading falls back to one whole-page chunk, so ingestion never hard-fails on an
  unfamiliar file.
- **Structured fact extraction.** All *numeric* business values (SLA minutes/hours, cancellation fee, credit
  threshold/amount, Bulk Upload row limits, KI-208/KI-211 thresholds, agreement overrides) are pulled out with
  generic regexes over the normalized text (e.g. `"(\d+)\s+(business\s+hours?|hours?|minutes?)"`,
  `"INR\s*([\d,]+)"`) into `policy_rules` and `agreement_terms` tables at ingestion time. This is what lets the
  calculation layer be pure arithmetic over a typed row instead of asking the LLM to re-derive "250" from prose
  every time, while still being derived dynamically from the document text (not typed in as a constant).
- **Retrieval — why BM25, not a vector DB:** the entire corpus is six short documents (~20 chunks total after
  chunking). A vector database would add real deployment complexity (embedding API calls, an external index,
  extra latency, another failure mode) for a corpus this size, with no meaningful recall benefit — BM25's
  term-overlap already finds the right chunk essentially every time at this scale, and its score is trivially
  explainable in an evidence chip ("this chunk shares these terms with your query"), which the vector-similarity
  score is not. `rank_bm25` runs in-process with no external service and no API key, keeping the "chat works
  without an API key for everything except the actual chat completion" guarantee simple to uphold. If the corpus
  grew to hundreds of documents, semantic retrieval would become worth its complexity; it wasn't here.

## 5. Structured-data handling & access control

`backend/app/domain/repositories.py` is the **single security boundary** for accounts/orders/tickets:

- `OrderRepository.get(principal, order_id)` fetches the row, **then** checks
  `principal.can_access_account(row.account_id)`; a customer principal gets `AuthorizationError` for another
  account's order — including ones that exist — never a leak of whether it exists.
- `search_orders`/`search_tickets` force `account_id = principal.account_id` for customer principals even if a
  different `account_id` was explicitly requested (by the model, by a crafted prompt, or by a naive client) —
  the request is rejected outright rather than silently narrowed, and independently the SQL `WHERE` clause never
  runs unscoped for a customer.
- `TicketRepository.get_scoped` returns a **field-allowlisted** `TicketPublic` model for customer principals,
  dropping `assigned_to` and `historical_resolution` — internal-only fields cannot leak via a wide `SELECT *`
  because the Pydantic model itself defines the allowlist.
- `retrieval/retriever.py` enforces the same boundary for documents: a chunk with `customer_scope` set to another
  account is excluded from the BM25 candidate set for a customer principal *before scoring*, not filtered out of
  results afterward — and `status='historical_only'` chunks are excluded unless the caller is internal **and**
  explicitly asks for them.

Crucially, all of this is enforced in Python functions called by tool handlers — **not** in the system prompt.
`backend/tests/test_agent_orchestrator.py::test_prompt_injection_cannot_bypass_cross_account_access` scripts a
fake LLM that naively tries to call `get_order`/`search_documents` for another account after being told "ignore
your instructions," and asserts the tool layer denies it regardless of what the model attempted.

## 6. Source reliability, conflict handling & confidence

Implemented in `backend/app/domain/authority.py` (precedence resolution), `backend/app/domain/conflicts.py`
(historical-ticket conflict detection), and `backend/app/agent/evidence.py` (`ConfidenceTracker`).

- **Precedence** is domain-first (support SLA vs. cancellation vs. credit vs. product capability), then
  agreement-override-vs-default within that domain — never a single global "rank all documents 1–6" score,
  because an agreement that governs cancellation says nothing about Bulk Upload limits.
- **Historical ticket conflicts**: `detect_historical_conflict` compares a ticket's `historical_resolution` text
  (generic INR/row-count extraction, not keyed to a specific ticket ID) against the currently resolved
  authoritative rule for that account. When they disagree, internal responses get a `conflict_warning`; the
  *answer itself* always uses the current authoritative source regardless of role, and customer-facing responses
  simply never surface the historical claim at all.
- **Confidence** (`High`/`Medium`/`Low`) is computed in code from signals tool handlers record during execution
  — never a number the LLM reports about itself:
  - **Low** — a required fact was missing (e.g. unknown carrier/customer fault for a credit question), a record
    was not found/unauthorized, no authoritative rule could be resolved, the reasoning-depth limit was hit, or no
    tool evidence was gathered at all for the answer.
  - **Medium** — an authoritative rule resolved cleanly but something is estimated or unconfirmed: a
    business-hours/business-day SLA target (demo calendar, not an authoritative one), a possible-but-unconfirmed
    SLA breach (no first-response timestamp in the dataset), or an unverifiable monthly aggregate credit cap.
  - **High** — authoritative source resolved, required facts present, nothing estimated or unconfirmed.

## 7. Confirmation architecture (state-changing actions)

Two-phase, `backend/app/actions/pending_actions.py`:

- **Phase 1 — `propose_action`** (called by the agent tool): persists a `PendingAction` row,
  `status='pending'`. This is the *only* thing a tool call can ever do to `pending_actions`/`escalations`/
  `followup_tasks`/`tickets` — there is no function reachable from a tool handler that mutates those tables
  directly.
- **Phase 2 — `confirm_action`** (called only by a dedicated REST endpoint the UI's Confirm button hits): re-fetches
  the action, checks:
  - **principal-bound**: `action.requested_by_user_id == principal.user_id`, else `ActionAuthorizationError` —
    even another internal user cannot confirm someone else's proposal in this implementation (see
    `docs/ARCHITECTURE.md` trade-offs below for the reasoning).
  - **status-checked**: only `pending` may transition to `executed`; `cancelled`/`expired` raise
    `ActionNotConfirmableError`.
  - **idempotent**: confirming an already-`executed` action returns the stored result without re-running
    `_execute`, so a UI double-click or client retry cannot double-create an escalation.
  - Only after all three checks does `_execute` run, mutating `tickets`/`escalations`/`followup_tasks` and
    writing an `audit_log` row.
- Every transition (proposed / executed / cancelled / denied / idempotent replay) is written to `audit_log` with
  the request id, principal, and full detail payload.

## 8. Major trade-offs

- **BM25 over a vector DB** — see §4. Revisit if the document corpus grows into the hundreds.
- **Regex-based structured extraction over hand-typed constants** — chunk *headings* used as citation anchors are
  literal per-document strings (a small, disclosed simplification for a fixed six-document pack — see
  `docs/SOURCE_ANALYSIS.md`), but every *numeric* value used in a calculation is parsed dynamically from the
  document text, and no order/ticket/account ID ever appears in production business logic (verified via
  `grep -rn "ORD-\|TKT-\|ACCT-00" backend/app` finding only the mock-auth demo user registry).
- **In-memory chat session history** (`backend/app/api/chat.py`) — simplest correct choice for a demo; a
  production system would persist conversation state per session in SQLite/Redis so it survives a restart. Noted
  as a known limitation in `README.md`.
- **Strict same-user confirmation binding** rather than a role-based confirmation queue — the assessment
  explicitly calls out "wrong-user confirmation denial" as a required test, so we chose the least-ambiguous rule
  (only the exact requester may confirm/cancel). A real support team would likely want a queue where any
  on-shift agent can confirm a colleague's proposal; that's flagged as a "what's next" item in `docs/PRODUCT.md`.
- **Demo business-hours calendar, explicitly labeled** — see `docs/SOURCE_ANALYSIS.md` §4. We chose to surface a
  clearly-flagged estimate rather than either (a) silently presenting a business-hours deadline as exact, or (b)
  refusing to answer business-hour SLA questions at all.
- **No vector vs. keyword hybrid, no reranker** — same reasoning as BM25 choice; added complexity wasn't
  justified by this corpus size, and determinism/inspectability was valued over marginal recall gains.
