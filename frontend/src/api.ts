import type {
  AccountSummary, AuditEntry, ChatResponse, DemoUser, IssueRadar, Meta, PendingAction,
} from "./types"

const BASE = "" // same-origin; Vite dev server proxies /api and /health to :8000

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, opts: RequestInit & { userId?: string | null } = {}): Promise<T> {
  const headers = new Headers(opts.headers)
  if (opts.userId) headers.set("X-Demo-User", opts.userId)
  if (opts.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json")

  const res = await fetch(BASE + path, { ...opts, headers })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      // `detail` is usually a plain string, but structured errors (e.g. the
      // chat endpoint's ai_not_configured response) send an object instead
      // -- never assume it's a string.
      if (typeof body.detail === "string") {
        detail = body.detail
      } else if (body.detail && typeof body.detail === "object") {
        detail = body.detail.message || JSON.stringify(body.detail)
      } else {
        detail = JSON.stringify(body)
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  demoUsers: () => request<DemoUser[]>("/api/demo-users"),
  me: (userId: string) => request<DemoUser & { is_internal: boolean }>("/api/me", { userId }),
  meta: () => request<Meta>("/api/meta"),
  accounts: (userId: string) => request<AccountSummary[]>("/api/accounts", { userId }),
  chat: (userId: string, message: string, sessionId?: string) =>
    request<ChatResponse>("/api/chat", {
      method: "POST", userId, body: JSON.stringify({ message, session_id: sessionId }),
    }),
  confirmAction: (userId: string, actionId: string) =>
    request<PendingAction>(`/api/actions/${actionId}/confirm`, { method: "POST", userId }),
  cancelAction: (userId: string, actionId: string) =>
    request<PendingAction>(`/api/actions/${actionId}/cancel`, { method: "POST", userId }),
  listActions: (userId: string) => request<PendingAction[]>("/api/actions", { userId }),
  issueRadar: (userId: string) => request<IssueRadar>("/api/issue-radar", { userId }),
  auditLog: (userId: string) => request<AuditEntry[]>("/api/audit-log", { userId }),
}
