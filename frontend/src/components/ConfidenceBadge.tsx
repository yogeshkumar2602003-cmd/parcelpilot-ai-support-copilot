const STYLES: Record<string, string> = {
  High: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  Medium: "bg-amber-50 text-amber-700 ring-amber-600/20",
  Low: "bg-rose-50 text-rose-700 ring-rose-600/20",
}

export default function ConfidenceBadge({ level }: { level: "High" | "Medium" | "Low" }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STYLES[level]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {level} confidence
    </span>
  )
}
