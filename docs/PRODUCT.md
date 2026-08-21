# Product Note

## Chosen additional client problem: Trust & Reliability

ParcelPilot's own brief for this problem states it plainly: *"Policies change, customer contracts may override
general rules, different systems may disagree, and previous support answers may be wrong. A confidently
incorrect answer or action would quickly reduce adoption."* That is the single highest-leverage risk for an AI
support system at a logistics company — a wrong SLA answer or a wrongly-denied cancellation fee is not a UX
nit, it is a contract dispute waiting to happen. We chose this over Proactive Issue Detection as the *primary*
problem, and still shipped a lightweight version of the second as a bonus (Issue Radar, §14 of the assessment),
because reliability is a prerequisite for a proactive feature being trustworthy in the first place — a radar that
misclassifies severity is worse than no radar.

### How the product addresses it

- **An explicit, code-enforced source-authority model** (`docs/ARCHITECTURE.md` §6) instead of "throw everything
  in a vector store and hope the LLM figures out precedence." Domain resolved first, then agreement-override vs.
  default within that domain. The deprecated Support Policy v2 is structurally excluded from normal answers, not
  merely told to be low-priority in a prompt.
- **Historical tickets are context, never authority**, and where a historical resolution's numeric claim
  actively conflicts with the current authoritative answer (`TKT-450`, `TKT-451`), the system detects it
  deterministically and (a) still gives the *correct current* answer, and (b) surfaces the conflict explicitly to
  internal staff so it can be corrected in training material or flagged as a past mistake — without exposing that
  internal embarrassment to the customer who asked the question.
- **Evidence-based confidence, not a self-reported LLM number.** Every answer's High/Medium/Low label is computed
  from what the tool calls actually returned (missing facts, resolved-vs-unresolved rule, estimate flags,
  unconfirmed-breach flags) — see `docs/ARCHITECTURE.md` §6. This means the confidence label cannot be gamed by
  the model being persuasively wrong; it degrades exactly when the *underlying evidence* is thin, which is what a
  trust signal needs to track.
- **Explicit uncertainty instead of confident guessing** in the two scenarios the brief calls out by name:
  refusing to state a first-response SLA breach as fact when the dataset has no first-response timestamp, and
  refusing to say a pickup failed when it's within SwiftShip's known 20-minute webhook delay window (KI-211).
  Both produce a Medium-confidence answer with an explicit `uncertainty_reason`, not a shrug buried in prose.
- **Access control enforced in the data layer, tested with an actual prompt-injection attempt**, so "trust" also
  covers "customers can't see each other's contracts," which is just as much a trust failure as a wrong SLA
  number.
- **Two-phase confirmation with an audit log** for anything that changes state, so no action is a surprise and
  every mutation is attributable and reviewable after the fact.

## What else I would build next (prioritized)

1. **Persisted conversation state.** Chat history currently lives in an in-memory dict keyed by session id
   (`backend/app/api/chat.py`) — fine for a demo, but it doesn't survive a restart and doesn't scale past one
   process. Move to a `conversations` table in SQLite (or Postgres in a real deployment) so history, evidence, and
   confidence are queryable/auditable after the fact, which matters a lot for a trust-focused product (you want
   to be able to show a customer or regulator exactly what the agent told someone and why).
2. **A real business-hours/holiday calendar per account**, replacing the labeled demo calendar, so business-hour
   SLA targets can be exact rather than estimated. This directly removes one of the two sources of Medium
   confidence in the current system.
3. **Role-based confirmation queues.** Right now only the exact requesting user can confirm their own proposed
   action (see the trade-off note in `docs/ARCHITECTURE.md` §8). A real support team wants any on-shift agent to
   be able to pick up and confirm a colleague's prepared escalation from a shared queue, with the audit log
   recording who actually confirmed it.
4. **Feedback loop from confirmed/executed actions and reopened tickets back into retrieval** — if an AI-assisted
   answer is later corrected by a human (ticket reopened, escalation overturned), that should demote the
   confidence of similar future answers until a human reviews the underlying source, closing the loop implied by
   the primary metric below.
5. **Expand Issue Radar's clustering beyond keyword overlap** — the current implementation is deliberately simple
   (deterministic, LLM-free, so it works without an API key) but a production version would use embeddings or an
   LLM-assisted clustering pass over a much larger ticket volume, with the current keyword approach kept as a
   fast, explainable fallback.
6. **Multi-document agreements and amendments.** The current model assumes one PDF per account; real customers
   accumulate amendments and addenda over a contract's life, which would need versioned agreement terms with
   their own effective-date precedence, not just one flat `agreement_terms` row per account.

## What I intentionally left out of this submission

- **Semantic/vector retrieval** — not justified at this corpus size; see `docs/ARCHITECTURE.md` §4.
- **A real identity provider** — authentication is intentionally mocked via a server-side demo-user registry
  (`backend/app/auth/principal.py`), as the assessment explicitly permits. The access-control *mechanism*
  (Principal-based, enforced in repositories, not in the prompt) is the part meant to generalize to a real IdP.
- **Deterministic severity auto-classification.** I chose to let the model classify P1/P2/P3 from the retrieved
  definitions rather than hand-coding keyword rules for it in production code, specifically to avoid
  overfitting to this assessment's exact ticket wording (see `docs/ARCHITECTURE.md` §3). Issue Radar uses a
  separate, explicitly-labeled *heuristic* classifier for its own bonus-feature purposes, which is a different
  (LLM-free, best-effort) use case.
- **Streaming responses** in the chat UI — the orchestrator returns a complete answer per turn; streaming partial
  tokens/tool-call progress would improve perceived latency but added UI complexity not central to the
  assessment's evaluation criteria.
- **Rate limiting / multi-tenant deployment hardening** (per-account quotas, abuse detection) — out of scope for
  a take-home demo, called out here so it's not mistaken for an oversight.

## Success metric

**Primary: Verified Resolution Rate** — the percentage of AI-assisted support cases (chat answers and prepared
actions) that are *not* subsequently corrected by a human within a defined window (e.g. 7 days): no policy
correction, no reopened ticket, no confirmed action later reversed, and no escalation raised specifically because
the AI's answer was wrong. This is the right primary metric for a Trust & Reliability-focused product because it
measures the thing that actually erodes adoption — being *confidently wrong* — rather than a proxy like
"resolved without human involvement," which rewards the system for sounding done even when it wasn't correct.

It's operationally cheap to compute from data this system already produces: join `audit_log` action outcomes
against later ticket status changes, and track any case where a customer or internal user's follow-up message
explicitly contradicts a prior AI answer.

**Guardrail metrics to track alongside it** (not chosen as primary, but should page someone if they move):
cross-account data exposure incidents (target: **zero**, tested continuously — see
`backend/tests/test_agent_orchestrator.py::test_prompt_injection_cannot_bypass_cross_account_access`), rate of
Low-confidence answers per week (a rising trend means the knowledge base needs updating faster than customers are
asking new kinds of questions), and mutation-without-confirmation attempts blocked (should always be handled, but
a rising count signals either a confused user base or a UI affordance problem worth fixing).
