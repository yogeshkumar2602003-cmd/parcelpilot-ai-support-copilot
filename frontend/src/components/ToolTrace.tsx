import { useState } from "react"
import type { ToolTraceEntry } from "../types"

export default function ToolTrace({ trace, richMode }: { trace: ToolTraceEntry[]; richMode: boolean }) {
  const [open, setOpen] = useState(false)
  if (!trace.length) return null

  return (
    <div className="mt-2 text-xs">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-slate-500 hover:text-slate-700"
      >
        <span>{open ? "▾" : "▸"}</span>
        <span>{trace.length} tool step{trace.length > 1 ? "s" : ""} used</span>
      </button>
      {open && (
        <ul className="mt-1 space-y-1 border-l-2 border-slate-200 pl-3">
          {trace.map((t, i) => (
            <li key={i} className="text-slate-600">
              <span className={`inline-block h-1.5 w-1.5 rounded-full mr-1.5 ${t.ok ? "bg-emerald-500" : "bg-rose-500"}`} />
              {t.label}
              {richMode && (
                <span className="text-slate-400"> — {t.summary}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
