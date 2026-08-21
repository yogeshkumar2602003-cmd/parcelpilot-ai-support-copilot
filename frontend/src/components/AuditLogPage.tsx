import { useEffect, useState } from "react"
import type { AuditEntry } from "../types"
import { api, ApiError } from "../api"

const EVENT_COLOR: Record<string, string> = {
  action_proposed: "text-slate-600",
  action_executed: "text-emerald-700",
  action_cancelled: "text-slate-400",
  action_confirm_denied: "text-rose-600",
  action_cancel_denied: "text-rose-600",
  action_confirm_idempotent_replay: "text-indigo-600",
}

export default function AuditLogPage({ userId }: { userId: string }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.auditLog(userId).then(setEntries).catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load audit log."))
  }, [userId])

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 sm:px-8">
      <h2 className="text-lg font-semibold text-slate-800">Audit Log</h2>
      <p className="mt-1 text-sm text-slate-500">Every proposed, confirmed, cancelled, or denied state-changing action.</p>
      {error && <p className="mt-4 text-sm text-rose-600">{error}</p>}
      <div className="mt-4 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2">Time</th>
              <th className="px-3 py-2">Event</th>
              <th className="px-3 py-2">User</th>
              <th className="px-3 py-2">Account</th>
              <th className="px-3 py-2">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {entries?.map((e) => (
              <tr key={e.id}>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-500">{e.timestamp?.slice(0, 19).replace("T", " ")}</td>
                <td className={`px-3 py-2 font-medium ${EVENT_COLOR[e.event_type] || "text-slate-700"}`}>{e.event_type}</td>
                <td className="px-3 py-2 text-slate-600">{e.user_id} <span className="text-slate-400">({e.role})</span></td>
                <td className="px-3 py-2 text-slate-500">{e.account_id || "—"}</td>
                <td className="px-3 py-2 text-xs text-slate-500">
                  <code className="break-all">{JSON.stringify(e.detail)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {entries?.length === 0 && <p className="px-3 py-6 text-center text-sm text-slate-400">No audit events yet.</p>}
      </div>
    </div>
  )
}
