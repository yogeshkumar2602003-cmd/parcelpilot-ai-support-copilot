export type Role = "customer" | "support" | "operations" | "admin"

export interface DemoUser {
  user_id: string
  display_name: string
  role: Role
  account_id: string | null
}

export interface Evidence {
  label: string
  source_file: string
  section: string | null
  page: number | null
  authority_category: string
  status: string
  detail: string | null
}

export interface PendingAction {
  action_id: string
  action_type: "create_escalation" | "update_ticket" | "create_followup_task"
  payload: Record<string, unknown>
  reason: string
  requested_by_user_id: string
  requested_by_role: Role
  account_id: string | null
  status: "pending" | "confirmed" | "cancelled" | "executed" | "expired"
  created_at: string
  decided_at: string | null
  decided_by_user_id: string | null
  result: Record<string, unknown> | null
}

export interface ToolTraceEntry {
  tool: string
  label: string
  input: Record<string, unknown>
  summary: string
  ok: boolean
}

export interface AgentAnswer {
  answer_markdown: string
  why: string | null
  evidence: Evidence[]
  confidence: "High" | "Medium" | "Low"
  uncertainty_reason: string | null
  conflict_warning: string | null
  pending_action: PendingAction | null
  tool_trace: ToolTraceEntry[]
}

export interface ChatResponse {
  session_id: string
  request_id: string
  answer: AgentAnswer
}

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  text: string
  answer?: AgentAnswer
}

export interface RadarCard {
  kind: string
  title: string
  detail: string
  ticket_ids: string[]
  account_ids: string[]
  severity_estimate: string | null
}

export interface IssueRadar {
  generated_at_snapshot: string
  open_ticket_count: number
  cards: RadarCard[]
}

export interface AuditEntry {
  id: string
  timestamp: string
  request_id: string | null
  user_id: string | null
  role: string | null
  account_id: string | null
  event_type: string
  detail: Record<string, unknown> | null
}

export interface AccountSummary {
  account_id: string
  account_name: string
  plan: string
  status: string
  csm: string | null
  contract_file: string | null
  premium_support: boolean
  notes: string | null
}

export interface Meta {
  dataset_snapshot: string
  currency: string
  source_documents: number
  /** Whether the backend has a server-side ANTHROPIC_API_KEY configured. Never the key itself. */
  ai_configured: boolean
}
