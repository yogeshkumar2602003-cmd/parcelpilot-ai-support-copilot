import { useEffect, useState } from "react"
import type { IssueRadar } from "../types"
import { api, ApiError } from "../api"

const KIND_META: Record<string, { label: string; color: string }> = {
  p1_candidate: { label: "P1 candidate", color: "bg-rose-50 text-rose-700 ring-rose-300" },
  known_issue_match: { label: "Known-issue match", color: "bg-indigo-50 text-indigo-700 ring-indigo-300" },
  sla_risk: { label: "SLA risk", color: "bg-amber-50 text-amber-700 ring-amber-300" },
  historical_conflict: { label: "Historical conflict", color: "bg-orange-50 text-orange-700 ring-orange-300" },
  recurring_pattern: { label: "Recurring pattern", color: "bg-slate-100 text-slate-700 ring-slate-300" },
}

export default function IssueRadarPage({ userId }: { userId: string }) {
  const [data, setData] = useState<IssueRadar | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.issueRadar(userId)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load Issue Radar."))
      .finally(() => setLoading(false))
  }, [userId])

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 sm:px-8">
      <h2 className="text-lg font-semibold text-slate-800">Issue Radar</h2>
      <p className="mt-1 text-sm text-slate-500">
        Internal-only proactive issue detection, computed deterministically from the current dataset snapshot
        {data && <> ({data.generated_at_snapshot}, {data.open_ticket_count} open tickets)</>}. No live LLM call is
        required for this page.
      </p>

      {loading && <p className="mt-6 text-sm text-slate-400">Loading…</p>}
      {error && <p className="mt-6 text-sm text-rose-600">{error}</p>}

      {data && data.cards.length === 0 && (
        <p className="mt-6 text-sm text-slate-500">No radar signals in the current dataset.</p>
      )}

      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        {data?.cards.map((c, i) => {
          const meta = KIND_META[c.kind] || { label: c.kind, color: "bg-slate-100 text-slate-700 ring-slate-300" }
          return (
            <div key={i} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${meta.color}`}>
                {meta.label}
              </span>
              <h3 className="mt-2 text-sm font-semibold text-slate-800">{c.title}</h3>
              <p className="mt-1 text-xs text-slate-500">{c.detail}</p>
              {c.ticket_ids.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {c.ticket_ids.map((t) => (
                    <span key={t} className="rounded bg-slate-50 px-1.5 py-0.5 text-[11px] font-mono text-slate-600 ring-1 ring-inset ring-slate-200">
                      {t}
                    </span>
                  ))}
                </div>
              )}
              {c.account_ids.length > 0 && (
                <p className="mt-1.5 text-[11px] text-slate-400">Accounts: {c.account_ids.join(", ")}</p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
