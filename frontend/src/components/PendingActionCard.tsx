import { useState } from "react"
import type { PendingAction } from "../types"
import { api, ApiError } from "../api"

const ACTION_LABELS: Record<string, string> = {
  create_escalation: "Create escalation",
  update_ticket: "Update ticket",
  create_followup_task: "Create follow-up task",
}

export default function PendingActionCard({
  action, userId, onChanged,
}: { action: PendingAction; userId: string; onChanged: (updated: PendingAction) => void }) {
  const [busy, setBusy] = useState<"confirm" | "cancel" | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handle(kind: "confirm" | "cancel") {
    setBusy(kind)
    setError(null)
    try {
      const updated = kind === "confirm" ? await api.confirmAction(userId, action.action_id) : await api.cancelAction(userId, action.action_id)
      onChanged(updated)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.")
    } finally {
      setBusy(null)
    }
  }

  const isPending = action.status === "pending"

  return (
    <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-amber-900">
          {ACTION_LABELS[action.action_type] || action.action_type}
        </span>
        <span className="rounded px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-amber-700 bg-amber-100">
          {action.status}
        </span>
      </div>
      <p className="mt-1 text-sm text-amber-900">{action.reason}</p>
      <dl className="mt-1 text-xs text-amber-800/80 space-y-0.5">
        {Object.entries(action.payload).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => (
          <div key={k}><span className="font-medium">{k}:</span> {String(v)}</div>
        ))}
      </dl>

      {isPending ? (
        <div className="mt-3 flex gap-2">
          <button
            disabled={busy !== null}
            onClick={() => handle("confirm")}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {busy === "confirm" ? "Confirming…" : "Confirm"}
          </button>
          <button
            disabled={busy !== null}
            onClick={() => handle("cancel")}
            className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:opacity-50"
          >
            {busy === "cancel" ? "Cancelling…" : "Cancel"}
          </button>
        </div>
      ) : (
        <p className="mt-2 text-xs italic text-amber-700">
          {action.status === "executed" ? "This action has been executed." : `This action was ${action.status}.`}
        </p>
      )}
      {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
    </div>
  )
}
