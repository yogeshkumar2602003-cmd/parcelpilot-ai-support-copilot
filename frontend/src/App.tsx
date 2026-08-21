import { useEffect, useState } from "react"
import type { DemoUser } from "./types"
import { api } from "./api"
import UserSwitcher from "./components/UserSwitcher"
import ChatPage from "./components/ChatPage"
import IssueRadarPage from "./components/IssueRadarPage"
import AuditLogPage from "./components/AuditLogPage"

type Tab = "chat" | "radar" | "audit"

const STORAGE_KEY = "parcelpilot_demo_user_id"

export default function App() {
  const [users, setUsers] = useState<DemoUser[]>([])
  const [userId, setUserId] = useState<string>(() => localStorage.getItem(STORAGE_KEY) || "")
  const [me, setMe] = useState<(DemoUser & { is_internal: boolean }) | null>(null)
  const [tab, setTab] = useState<Tab>("chat")
  const [snapshot, setSnapshot] = useState<string>("")
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    api.demoUsers()
      .then((u) => {
        setUsers(u)
        if (!userId && u.length) setUserId(u.find((x) => x.role === "support")?.user_id || u[0].user_id)
      })
      .catch(() => setLoadError("Could not reach the ParcelPilot backend API. Is it running?"))
    api.meta().then((m) => setSnapshot(m.dataset_snapshot)).catch(() => {})
    // Runs once on mount only: picks an initial demo user if none is stored yet.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!userId) return
    localStorage.setItem(STORAGE_KEY, userId)
    api.me(userId).then(setMe).catch(() => setMe(null))
    setTab("chat")
  }, [userId])

  if (loadError) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-50 px-6 text-center">
        <div>
          <p className="text-lg font-semibold text-slate-700">ParcelPilot Support Copilot</p>
          <p className="mt-2 text-sm text-rose-600">{loadError}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen flex-col bg-slate-50">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-2.5 sm:px-8">
        <div className="flex items-center gap-3">
          <span className="text-base font-bold text-indigo-700">ParcelPilot</span>
          <span className="text-sm text-slate-400">AI Support Copilot</span>
          {me && (
            <span
              className={`ml-2 rounded-full px-2 py-0.5 text-xs font-medium ${
                me.is_internal ? "bg-indigo-50 text-indigo-700" : "bg-emerald-50 text-emerald-700"
              }`}
            >
              {me.is_internal ? "Internal mode" : `Customer mode · ${me.account_id}`}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {snapshot && <span className="hidden text-xs text-slate-400 sm:inline">Dataset snapshot: {snapshot}</span>}
          <UserSwitcher users={users} selectedId={userId} onSelect={setUserId} />
        </div>
      </header>

      {me && (
        <nav className="flex gap-1 border-b border-slate-200 bg-white px-4 sm:px-8">
          <TabButton label="Chat" active={tab === "chat"} onClick={() => setTab("chat")} />
          {me.is_internal && <TabButton label="Issue Radar" active={tab === "radar"} onClick={() => setTab("radar")} />}
          {me.is_internal && <TabButton label="Audit Log" active={tab === "audit"} onClick={() => setTab("audit")} />}
        </nav>
      )}

      <main className="min-h-0 flex-1">
        {!me ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">Loading…</div>
        ) : tab === "chat" ? (
          <ChatPage key={me.user_id} user={me} />
        ) : tab === "radar" ? (
          <IssueRadarPage userId={me.user_id} />
        ) : (
          <AuditLogPage userId={me.user_id} />
        )}
      </main>
    </div>
  )
}

function TabButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
        active ? "border-indigo-600 text-indigo-700" : "border-transparent text-slate-500 hover:text-slate-700"
      }`}
    >
      {label}
    </button>
  )
}
