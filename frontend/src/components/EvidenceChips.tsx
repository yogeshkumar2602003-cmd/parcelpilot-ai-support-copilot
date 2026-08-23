import type { Evidence } from "../types"

const STATUS_STYLE: Record<string, string> = {
  current: "bg-slate-100 text-slate-700 ring-slate-400/30",
  historical_only: "bg-orange-50 text-orange-700 ring-orange-400/40",
}

export default function EvidenceChips({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {evidence.map((e, i) => (
        <span
          key={i}
          title={e.detail || e.label}
          className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${STATUS_STYLE[e.status] || STATUS_STYLE.current}`}
        >
          {e.label}
          {e.page != null && <span className="ml-1 opacity-70">p.{e.page}</span>}
          {e.status === "historical_only" && <span className="ml-1 opacity-70">(historical)</span>}
        </span>
      ))}
    </div>
  )
}
