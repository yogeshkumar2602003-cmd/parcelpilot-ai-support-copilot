# Source Pack Analysis

This is a verified analysis of the supplied ParcelPilot candidate data pack, produced by programmatically
extracting and reading every file (`pypdf` for the six PDFs, `openpyxl` for the workbook, `python-docx` for the
assessment brief) rather than assuming their contents. All numbers below were cross-checked against the actual
extracted text during implementation (see `backend/app/ingestion/` and `backend/scripts/evaluate.py`).

**Important scoping note:** `CalQuity AI Engineer — Job Description & AI Agent Assessment.docx` is the
*development specification* for this project. It is read here, by a human/engineer, to understand what to build.
It is **not** ingested into the running application and is **not** part of the ParcelPilot chatbot's runtime
knowledge base. The runtime knowledge base is exactly: the six PDFs + the Excel workbook.

## 1. Document inventory and status

| # | File | Doc type | Status (as printed in the doc) | Effective / updated |
|---|------|----------|-------|----|
| 01 | Support_Policy_v3_CURRENT.pdf | `support_policy` | **CURRENT** | Effective 1 May 2026 |
| 02 | Support_Policy_v2_DEPRECATED.pdf | `support_policy` | **DEPRECATED** → treated as `historical_only` | Effective 1 Jan 2025, superseded by v3 |
| 03 | Cancellation_and_Service_Credit_SOP_v4.pdf | `cancellation_sop` | **CURRENT** | Effective 15 June 2026 |
| 04 | Product_Operations_Guide_and_Known_Issues.pdf | `product_ops_guide` | **CURRENT** | Updated 14 Aug 2026 |
| 05 | Northstar_Logistics_Enterprise_Agreement.pdf | `customer_agreement` | **ACTIVE** (ACCT-001) | Term 1 Jan 2026 – 31 Dec 2026 |
| 06 | LumenWorks_Service_Agreement.pdf | `customer_agreement` | **ACTIVE** (ACCT-002) | Term 1 Mar 2026 – 28 Feb 2027 |

The status string is read from each document's own "Status:" line at ingestion time (regex-extracted, not
hardcoded per filename), so `DEPRECATED` always maps to `historical_only` regardless of which specific file it
appears in.

## 2. Source precedence model (domain-first, then precedence within domain)

Support Policy v3 §1 states the precedence explicitly: *"When sources conflict, use the signed customer
agreement first, then the current support policy, then current product documentation. Historical tickets and
internal notes are context only."* We generalized this into a domain-first model (see
`backend/app/domain/authority.py`):

1. Identify the **domain** of the question: support SLA, cancellation, failed-pickup credit, or product
   capability/known issue. These domains are answered by different documents and are never cross-ranked on one
   numeric scale.
2. Within that domain: does the account's **active signed agreement** address this subject? If yes, it overrides
   the generic default **only for what it addresses** (e.g. Northstar's agreement overrides cancellation fees but
   is silent on Bulk Upload row limits, so Bulk Upload questions still fall through to the Product Operations
   Guide).
3. Otherwise, use the **current** authoritative document for that domain.
4. **Deprecated Support Policy v2** is excluded from step 3 entirely — it is never "the current support policy."
5. **Structured workbook data** (accounts/orders/tickets) supplies operational facts at the dataset snapshot —
   never policy.
6. **Historical ticket `historical_resolution` text is context only**, never authority, regardless of how
   confident or specific it sounds.

## 3. Verified numeric facts (extracted dynamically, not hand-typed into business logic)

### Support Policy v3 (current) — default first-response targets

| Plan | P1 | P2 | P3 |
|---|---|---|---|
| Enterprise | 30 minutes, 24x7 | 2 hours | 1 business day |
| Growth | 2 business hours | 4 business hours | 2 business days |
| Standard | 4 business hours | 1 business day | 2 business days |

Severity definitions (§2): P1 = complete production outage preventing all shipment creation, confirmed security
incident, or suspected credential exposure, or similar with no workaround. P2 = major feature unavailable/
degraded but core operations possible or a workaround exists. P3 = minor defect/how-to/config/limited impact.

### Support Policy v2 (DEPRECATED — historical only)

| Plan | P1 | P2 | P3 |
|---|---|---|---|
| Enterprise | 1 hour | 4 hours | 2 business days |
| Growth | 4 business hours | 1 business day | 3 business days |
| Standard | 8 business hours | 2 business days | 3 business days |

These numbers **must never** be used to answer a current request. The document is retained only for
"explain the history / what changed" or explicit conflict-resolution queries by internal users.

### Cancellation & Service Credit SOP v4 (current)

- DRAFT: cancel with no fee.
- BOOKED, not yet PICKED_UP: no fee **within 30 minutes** of booking (inclusive of the boundary); **after 30
  minutes**, INR 250, unless a customer agreement explicitly waives it.
- PICKED_UP: cannot cancel; use return-to-origin.
- DELIVERED: cannot cancel.
- Failed-pickup credit (default): pickup **more than 2 hours** past the scheduled pickup-window end, carrier at
  fault, no customer-caused issue → credit = lower of INR 500 or 10% of shipment fee.
- Any individual credit above INR 1,000 requires manager approval.
- Do not promise a credit when carrier fault / timing / customer fault is unknown; flag conflicting data for
  verification before any state-changing action.

### Product Operations Guide (current)

- Bulk Upload: Growth & Enterprise, up to **5,000 rows** per CSV. Standard: not included.
- **KI-208** (Investigating): intermittent failures on CSVs **above ~3,000 rows**, even though the supported
  limit remains 5,000. Workaround: split into files below 3,000 rows. Individual shipment creation unaffected.
- **KI-211** (Monitoring): SwiftShip pickup-confirmation webhook can arrive **up to 20 minutes late** — a parcel
  may be physically collected while ParcelPilot still shows BOOKED. Verify carrier status or wait through the
  delay window before telling a customer a pickup did not occur.
- **KI-176** (Resolved 18 Jul 2026): address validation. Must not be used to explain unrelated new incidents.

### Northstar Logistics Enterprise Agreement (ACCT-001, ACTIVE)

- Support overrides: P1 **15 minutes, 24x7**; P2 **1 hour**; P3 **8 business hours**.
- Cancellation: any BOOKED shipment before pickup may be cancelled with **no fee**, regardless of elapsed time.
  After PICKED_UP, standard return-to-origin applies (agreement does not override this).
- Service credits: monthly aggregate cap **INR 5,000**; otherwise current SOP applies (agreement is silent on
  threshold/amount, so those fall through to the default SOP).
- CSM: Priya Mehta.

### LumenWorks Service Agreement (ACCT-002, ACTIVE)

- Plan: Growth. Support: P1 **2 business hours**, P2 **4 business hours**, P3 **2 business days**; **no
  weekend or after-hours coverage**.
- Cancellation: **no special waiver** — current SOP applies as-is.
- Failed-pickup credit: pickup **more than 4 hours** past window end, carrier at fault, customer not at fault →
  **fixed INR 300**. This explicitly replaces the SOP's default threshold *and* amount (not additive).

## 4. Dataset snapshot

The workbook's `README` sheet states: **`2026-08-16 11:00 Asia/Kolkata`** (cell literal, not a computed
formula). This is a **Sunday**. The application reads this value at ingestion time
(`backend/app/ingestion/workbook_ingest.py`) and uses it as "now" for every dataset-relative calculation
(`backend/app/domain/snapshot.py`) — the host machine's real clock is never consulted for these calculations.
All workbook order/ticket timestamps are bare `YYYY-MM-DD HH:MM` strings with no timezone; per the assessment
instructions we treat them as `Asia/Kolkata`.

Because the snapshot is a Sunday and LumenWorks' agreement explicitly excludes weekend/after-hours coverage, and
because the pack supplies no holiday calendar or exact operating-hours definition, **business-hour/business-day
SLA targets cannot be computed as exact deadlines from the supplied sources alone**. We implement a clearly
labeled, configurable demo business-hours calendar (Mon–Fri, 09:00–18:00, no holidays;
`backend/app/domain/business_calendar.py`) purely to produce an *estimate*, and every such estimate is flagged
`is_estimate=true` with an explicit assumption note rather than being presented as authoritative. 24x7 targets
(e.g. Northstar P1) are computed as exact wall-clock deltas since no calendar ambiguity applies to them.

## 5. Account-specific overrides (structured, from `accounts` sheet + agreements)

| Account | Plan | Agreement in pack? | Notable override |
|---|---|---|---|
| ACCT-001 Northstar Logistics | Enterprise | Yes (05) | Faster SLA, fee-free cancellation, INR 5,000 monthly credit cap |
| ACCT-002 LumenWorks | Growth | Yes (06) | Standard SLA per plan default but no weekend/after-hours; fixed INR 300 credit at >4h |
| ACCT-003 Beacon Retail | Standard | **No** — "standard policies apply" per accounts sheet notes | None; pure defaults |
| ACCT-004 Axis Labs | Enterprise | **No** — "standard Enterprise support policy applies" | None; pure defaults |

## 6. Known issues and important edge cases the implementation must respect

1. **No first-response timestamp.** The `tickets` sheet has `created_at` and `last_customer_message_at` but no
   agent first-response time. An open ticket older than its SLA target only makes a breach *possible*, never
   *confirmed*. The system states this explicitly rather than asserting a breach as fact.
2. **KI-211 webhook delay vs. customer-reported pickup.** A ticket reporting "driver already collected it but
   ParcelPilot still shows BOOKED" must not be answered as "pickup failed" — it is squarely the known 20-minute
   webhook delay window, and the system should recommend verification/waiting rather than asserting failure.
3. **Deliberately misleading historical tickets.**
   - `TKT-450` (closed): historical resolution claims Northstar owed an INR 250 fee after 30 minutes. This
     directly contradicts the now-active Northstar agreement's unconditional pre-pickup cancellation waiver. The
     system must never repeat this historical answer for a current Northstar cancellation question, and internal
     mode should surface the conflict.
   - `TKT-451` (closed): historical resolution claims "Growth plan only supports 3,000 rows" for Bulk Upload.
     This conflicts with the current Product Operations Guide (supported limit remains 5,000; 3,000 is only
     KI-208's *intermittent-failure* threshold, not a hard cap). Must not be repeated as current guidance.
4. **Carrier/customer fault ambiguity.** Several orders have both `carrier_fault` and `customer_fault` populated
   as real booleans (not always both `False`) — e.g. `ORD-2002` has `carrier_fault=True`,
   `customer_fault=False`, enabling a confident credit determination. Where these are `None`/unknown for a given
   order, the system must refuse to promise a credit rather than guessing.
5. **Strict boundary language matters.** "within 30 minutes" is inclusive (`<= 30`); "after 30 minutes" is
   exclusive (`> 30`); "more than 2 hours" / "more than 4 hours" are both strict (`>`), not `>=`. Verified via
   dedicated boundary unit tests.
6. **Deprecated file must stay generally invisible.** Support Policy v2 is excluded from default document
   retrieval and from default SLA resolution; it is retrievable only by an internal principal explicitly asking
   about historical policy or a version conflict (`search_documents(include_historical=true)`, internal-only).
7. **The assessment DOCX must never leak into chat answers.** It is used only during development/spec-reading; it
   is not copied into `backend/data/`, not parsed by the ingestion pipeline, and not retrievable by any tool.
