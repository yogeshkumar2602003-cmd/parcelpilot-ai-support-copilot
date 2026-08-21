import { useRef, useState, useEffect } from "react"
import ReactMarkdown from "react-markdown"
import type { ChatMessage, DemoUser, PendingAction } from "../types"
import { api, ApiError } from "../api"
import ConfidenceBadge from "./ConfidenceBadge"
import EvidenceChips from "./EvidenceChips"
import ToolTrace from "./ToolTrace"
import PendingActionCard from "./PendingActionCard"

const CUSTOMER_SUGGESTIONS = [
  "Can I cancel my order without a fee?",
  "A pickup is three hours late because of carrier fault. Should I get a service credit?",
  "What is your P1 support SLA for my plan?",
]
const INTERNAL_SUGGESTIONS = [
  "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.",
  "TKT-502 is a 4,200-row bulk upload failure for LumenWorks -- what's going on and what severity is this?",
  "TKT-504: SwiftShip still shows BOOKED about 10 minutes after the driver picked up. What should I tell the customer?",
  "Escalate TKT-501, it looks like a full production outage.",
  "Growth P1 SLA is 4 business hours, correct?",
]

export default function ChatPage({ user }: { user: DemoUser & { is_internal: boolean } }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, loading])

  async function send(text: string) {
    if (!text.trim() || loading) return
    const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", text }
    setMessages((m) => [...m, userMsg])
    setInput("")
    setLoading(true)
    setError(null)
    try {
      const res = await api.chat(user.user_id, text, sessionId)
      setSessionId(res.session_id)
      setMessages((m) => [...m, { id: crypto.randomUUID(), role: "assistant", text: res.answer.answer_markdown, answer: res.answer }])
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        setError("The AI agent is not configured yet: ANTHROPIC_API_KEY is missing on the server. Data exploration, calculations, and Issue Radar still work without it.")
      } else {
        setError(e instanceof ApiError ? e.message : "Something went wrong talking to the agent.")
      }
    } finally {
      setLoading(false)
    }
  }

  function onPendingActionChanged(msgId: string, updated: PendingAction) {
    setMessages((msgs) =>
      msgs.map((m) => (m.id === msgId && m.answer ? { ...m, answer: { ...m.answer, pending_action: updated } } : m)),
    )
  }

  const suggestions = user.is_internal ? INTERNAL_SUGGESTIONS : CUSTOMER_SUGGESTIONS

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-8">
        {messages.length === 0 && (
          <div className="mx-auto max-w-2xl pt-8 text-center">
            <h2 className="text-lg font-semibold text-slate-800">
              {user.is_internal ? "ParcelPilot Internal Support & Operations Agent" : "ParcelPilot Support"}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              {user.is_internal
                ? "Investigate orders, tickets, and policy across authorized accounts."
                : `Ask about your account (${user.account_id}) -- orders, cancellations, credits, and SLAs.`}
            </p>
            <div className="mt-6 grid gap-2 sm:grid-cols-1">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-left text-sm text-slate-700 shadow-sm hover:border-indigo-300 hover:bg-indigo-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="mx-auto max-w-2xl space-y-4">
          {messages.map((m) => (
            <div key={m.id} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div
                className={
                  m.role === "user"
                    ? "max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2.5 text-sm text-white"
                    : "max-w-[92%] rounded-2xl rounded-bl-sm bg-white px-4 py-3 text-sm text-slate-800 shadow-sm ring-1 ring-slate-200"
                }
              >
                {m.role === "user" ? (
                  m.text
                ) : (
                  <>
                    <div className="markdown-body">
                      <ReactMarkdown>{m.text}</ReactMarkdown>
                    </div>
                    {m.answer && (
                      <>
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <ConfidenceBadge level={m.answer.confidence} />
                        </div>
                        {m.answer.uncertainty_reason && (
                          <p className="mt-2 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800 ring-1 ring-inset ring-amber-200">
                            ⚠ {m.answer.uncertainty_reason}
                          </p>
                        )}
                        {m.answer.conflict_warning && (
                          <p className="mt-2 rounded-md bg-orange-50 px-2.5 py-1.5 text-xs text-orange-800 ring-1 ring-inset ring-orange-200">
                            ⚡ Historical conflict: {m.answer.conflict_warning}
                          </p>
                        )}
                        <EvidenceChips evidence={m.answer.evidence} />
                        <ToolTrace trace={m.answer.tool_trace} richMode={user.is_internal} />
                        {m.answer.pending_action && (
                          <PendingActionCard
                            action={m.answer.pending_action}
                            userId={user.user_id}
                            onChanged={(u) => onPendingActionChanged(m.id, u)}
                          />
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-sm bg-white px-4 py-3 text-sm text-slate-400 shadow-sm ring-1 ring-slate-200">
                <span className="inline-flex gap-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.3s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.15s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400" />
                </span>
              </div>
            </div>
          )}

          {error && (
            <div className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-inset ring-rose-200">
              {error}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); send(input) }}
        className="border-t border-slate-200 bg-white px-4 py-3 sm:px-8"
      >
        <div className="mx-auto flex max-w-2xl gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={user.is_internal ? "Ask about an order, ticket, account, or policy…" : "Ask a question about your account…"}
            className="flex-1 rounded-full border border-slate-300 px-4 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="rounded-full bg-indigo-600 px-5 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  )
}
