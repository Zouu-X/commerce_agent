import { useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"
import { failedTraceUrl, fetchRuntimeInfo, runtimeLabel } from "./runtime"
import type { RuntimeInfo } from "./runtime"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"

type DemoCustomer = {
  id: string
  display_name: string
  membership_level: string
  sample_prompts: string[]
}

type DemoContext = {
  tenant_id: string
  tenant_name: string
  store_id: string
  store_name: string
  customers: DemoCustomer[]
}

type ChatMessage = {
  id: string
  role: "user" | "assistant"
  content: string
  sources?: Array<{ title: string; version: string }>
}

type AgentTurn = {
  trace_id: string
  conversation_id: string
  message: ChatMessage
  model_loops: number
  tool_calls: number
  input_tokens: number
  output_tokens: number
}

type AgentErrorPayload = {
  detail?: string
  trace_id?: string
}

function requestHeaders(context: DemoContext, customer: DemoCustomer) {
  return {
    "Content-Type": "application/json",
    "X-Tenant-Id": context.tenant_id,
    "X-Store-Id": context.store_id,
    "X-Customer-Id": customer.id,
  }
}

export function CustomerChat() {
  const [contexts, setContexts] = useState<DemoContext[]>([])
  const [selectedStore, setSelectedStore] = useState("")
  const [selectedCustomer, setSelectedCustomer] = useState("")
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [error, setError] = useState("")
  const [lastTurn, setLastTurn] = useState<AgentTurn | null>(null)
  const [failedTraceId, setFailedTraceId] = useState<string | null>(null)
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null)

  const context = useMemo(
    () => contexts.find((item) => item.store_id === selectedStore) ?? contexts[0],
    [contexts, selectedStore],
  )
  const customer = useMemo(
    () => context?.customers.find((item) => item.id === selectedCustomer) ?? context?.customers[0],
    [context, selectedCustomer],
  )

  useEffect(() => {
    void fetch(`${API_BASE}/api/v1/demo/contexts`)
      .then((response) => {
        if (!response.ok) throw new Error("无法读取 Demo 身份")
        return response.json() as Promise<DemoContext[]>
      })
      .then((data) => {
        setContexts(data)
        setSelectedStore(data[0]?.store_id ?? "")
        setSelectedCustomer(data[0]?.customers[0]?.id ?? "")
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Demo 身份加载失败")
      })
  }, [])

  useEffect(() => {
    void fetchRuntimeInfo(API_BASE)
      .then(setRuntime)
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "运行时信息加载失败")
      })
  }, [])

  function resetConversation() {
    setConversationId(null)
    setMessages([])
    setInput("")
    setLastTurn(null)
    setFailedTraceId(null)
    setError("")
  }

  function changeStore(storeId: string) {
    const nextContext = contexts.find((item) => item.store_id === storeId)
    setSelectedStore(storeId)
    setSelectedCustomer(nextContext?.customers[0]?.id ?? "")
    resetConversation()
  }

  function changeCustomer(customerId: string) {
    setSelectedCustomer(customerId)
    resetConversation()
  }

  async function ensureConversation(activeContext: DemoContext, activeCustomer: DemoCustomer) {
    if (conversationId) return conversationId
    const response = await fetch(`${API_BASE}/api/v1/conversations`, {
      method: "POST",
      headers: requestHeaders(activeContext, activeCustomer),
    })
    if (!response.ok) throw new Error(`会话创建失败（${response.status}）`)
    const payload = (await response.json()) as { id: string }
    setConversationId(payload.id)
    return payload.id
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault()
    const content = input.trim()
    if (!content || !context || !customer || sending) return

    const userMessage: ChatMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content,
    }
    setMessages((current) => [...current, userMessage])
    setInput("")
    setSending(true)
    setError("")
    setLastTurn(null)
    setFailedTraceId(null)
    try {
      const activeConversation = await ensureConversation(context, customer)
      const response = await fetch(
        `${API_BASE}/api/v1/conversations/${activeConversation}/messages`,
        {
          method: "POST",
          headers: requestHeaders(context, customer),
          body: JSON.stringify({ content }),
        },
      )
      const payload = (await response.json()) as AgentTurn | AgentErrorPayload
      if (!response.ok) {
        const failure = payload as AgentErrorPayload
        setFailedTraceId(failure.trace_id ?? null)
        throw new Error(failure.detail ?? `Agent 请求失败（${response.status}）`)
      }
      const turn = payload as AgentTurn
      setMessages((current) => [...current, turn.message])
      setLastTurn(turn)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Agent 暂时无法回复")
    } finally {
      setSending(false)
    }
  }

  const prompts = customer?.sample_prompts ?? []
  const failedTraceHref = failedTraceId && context
    ? failedTraceUrl(failedTraceId, {
        tenantId: context.tenant_id,
        storeId: context.store_id,
      })
    : null

  return (
    <main className="app-shell customer-shell">
      <header className="topbar">
        <div className="brand-mark">CA</div>
        <div className="brand-copy">
          <strong>Commerce Agent</strong>
          <span>{runtime ? `${runtime.provider} customer assistant` : "Customer assistant"}</span>
        </div>
        <a className="page-switch customer-page-switch" href="/merchant">商户后台</a>
        <a className="docs-link" href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">
          API 文档 ↗
        </a>
      </header>

      <section className="customer-hero">
        <div>
          <p className="eyebrow">CUSTOMER EXPERIENCE · TOOL-CALLING AGENT</p>
          <h1>今天想了解什么？</h1>
          <p className="hero-copy">
            选择一个真实 Demo 顾客后提问。商品、订单、物流和政策都来自数据库；退款、发券和取消订单只会创建待审批请求。
          </p>
        </div>
        <div className="model-pill"><span className="model-dot" />{runtimeLabel(runtime)}</div>
      </section>

      <section className="customer-context panel" aria-label="顾客身份">
        <label>
          <span>店铺</span>
          <select disabled={sending} value={context?.store_id ?? ""} onChange={(event) => changeStore(event.target.value)}>
            {contexts.map((item) => <option key={item.store_id} value={item.store_id}>{item.tenant_name} / {item.store_name}</option>)}
          </select>
        </label>
        <label>
          <span>模拟顾客</span>
          <select disabled={sending} value={customer?.id ?? ""} onChange={(event) => changeCustomer(event.target.value)}>
            {context?.customers.map((item) => <option key={item.id} value={item.id}>{item.display_name} · {item.membership_level}</option>)}
          </select>
        </label>
        <div className="identity-note">
          <strong>服务端可信身份</strong>
          <span>tenant / store / customer 不会交给模型修改</span>
        </div>
      </section>

      <section className="chat-layout">
        <aside className="example-panel panel">
          <span className="section-kicker">可用示例</span>
          <h2>这些问题都有真实数据</h2>
          <p>示例会跟随当前顾客变化，订单号只属于所选账号。</p>
          <div className="prompt-list">
            {prompts.map((prompt) => (
              <button disabled={sending} key={prompt} onClick={() => setInput(prompt)} type="button">
                <span>↗</span>{prompt}
              </button>
            ))}
          </div>
        </aside>

        <section className="chat-panel panel" aria-live="polite">
          <div className="messages">
            <article className="message message-assistant">
              <div className="message-avatar">AI</div>
              <div>
                <span>客服 Agent</span>
                <p>你好，我可以查询当前账号下的商品、订单、物流和店铺政策，也可以为敏感操作创建人工审批申请。</p>
              </div>
            </article>
            {messages.map((message) => (
              <article className={`message message-${message.role}`} key={message.id}>
                <div className="message-avatar">{message.role === "user" ? "你" : "AI"}</div>
                <div>
                  <span>{message.role === "user" ? customer?.display_name : "客服 Agent"}</span>
                  <p>{message.content}</p>
                  {!!message.sources?.length && (
                    <div className="message-sources" aria-label="回答依据">
                      <small>回答依据</small>
                      {message.sources.map((source) => (
                        <span key={`${source.title}-${source.version}`}>
                          《{source.title}》{source.version ? ` · ${source.version}` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </article>
            ))}
            {sending && <article className="message message-assistant"><div className="message-avatar">AI</div><div><span>客服 Agent</span><p className="thinking-indicator">正在分析并查询可信数据…</p></div></article>}
          </div>

          {error && (
            <div className="error-banner chat-error">
              <span>{error}</span>
              {failedTraceHref && <a href={failedTraceHref}>查看失败 Trace →</a>}
            </div>
          )}

          <form className="composer" onSubmit={(event) => void sendMessage(event)}>
            <textarea
              aria-label="输入问题"
              disabled={!customer || sending}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault()
                  event.currentTarget.form?.requestSubmit()
                }
              }}
              placeholder="例如：帮我查订单 AUR-202607-0001"
              rows={3}
              value={input}
            />
            <div className="composer-footer">
              <span>Enter 发送 · Shift + Enter 换行</span>
              <button disabled={!input.trim() || !customer || sending} type="submit">{sending ? "处理中…" : "发送问题"}</button>
            </div>
          </form>

          {lastTurn && (
            <footer className="turn-meta">
              <span>{lastTurn.model_loops} 次模型调用 · {lastTurn.tool_calls} 次工具调用 · {lastTurn.input_tokens + lastTurn.output_tokens} tokens</span>
              <a href="/merchant">前往商户后台查看 Trace 与审批 →</a>
            </footer>
          )}
        </section>
      </section>
    </main>
  )
}
