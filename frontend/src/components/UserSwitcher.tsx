import type { DemoUser } from "../types"

const ROLE_LABEL: Record<string, string> = {
  customer: "Customer", support: "Support", operations: "Operations", admin: "Admin",
}

export default function UserSwitcher({
  users, selectedId, onSelect,
}: { users: DemoUser[]; selectedId: string; onSelect: (id: string) => void }) {
  const customers = users.filter((u) => u.role === "customer")
  const internal = users.filter((u) => u.role !== "customer")

  return (
    <select
      value={selectedId}
      onChange={(e) => onSelect(e.target.value)}
      className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-700 shadow-sm focus:border-indigo-400 focus:outline-none"
    >
      <optgroup label="Internal (ParcelPilot staff)">
        {internal.map((u) => (
          <option key={u.user_id} value={u.user_id}>{u.display_name} · {ROLE_LABEL[u.role]}</option>
        ))}
      </optgroup>
      <optgroup label="Customer accounts">
        {customers.map((u) => (
          <option key={u.user_id} value={u.user_id}>{u.display_name}</option>
        ))}
      </optgroup>
    </select>
  )
}
