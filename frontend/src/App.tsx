import { useCallback, useEffect, useMemo, useState } from "react"

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"

type DemoContext = {
  tenant_id: string
  tenant_name: string
  store_id: string
  store_name: string
}

type AuditLog = {
  id: string
  event_index: number
  event_type: string
  actor_type: string
  actor_id: string
  created_at: string
}

type PendingAction = {
  id: string
  action_type: "refund" | "issue_coupon" | "cancel_order"
  status: "pending" | "approved" | "rejected" | "executing" | "succeeded" | "failed"
  customer_id: string
  payload: Record<string, unknown>
  result: Record<string, unknown> | null
  failure_code: string | null
  rejection_reason: string | null
  created_at: string
  reviewed_by: string | null
  audit_logs: AuditLog[]
}

const actionLabels: Record<PendingAction["action_type"], string> = {
  cancel_order: "取消订单",
  refund: "订单退款",
  issue_coupon: "发放补偿券",
}

const statusLabels: Record<PendingAction["status"], string> = {
  pending: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  executing: "执行中",
  succeeded: "已完成",
  failed: "执行失败",
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value))
}

function payloadSummary(action: PendingAction) {
  const order = typeof action.payload.order_number === "string" ? action.payload.order_number : null
  const amount = typeof action.payload.amount === "string" ? `¥${action.payload.amount}` : null
  return [order, amount].filter(Boolean).join(" · ") || "当前顾客"
}

async function fetchActions(filter: string, headers: Record<string, string>) {
  const query = filter === "all" ? "" : `?status=${filter}`
  const response = await fetch(`${API_BASE}/api/v1/approvals${query}`, { headers })
  if (!response.ok) throw new Error(`审批队列加载失败（${response.status}）`)
  return (await response.json()) as PendingAction[]
}

export function App() {
  const [contexts, setContexts] = useState<DemoContext[]>([])
  const [selectedStore, setSelectedStore] = useState("")
  const [filter, setFilter] = useState("pending")
  const [approverId, setApproverId] = useState("ops-reviewer@example.com")
  const [actions, setActions] = useState<PendingAction[]>([])
  const [loading, setLoading] = useState(true)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [error, setError] = useState("")

  const context = useMemo(
    () => contexts.find((item) => item.store_id === selectedStore) ?? contexts[0],
    [contexts, selectedStore],
  )

  useEffect(() => {
    void fetch(`${API_BASE}/api/v1/demo/contexts`)
      .then((response) => {
        if (!response.ok) throw new Error("无法读取 Demo 店铺")
        return response.json() as Promise<DemoContext[]>
      })
      .then((data) => {
        setContexts(data)
        setSelectedStore(data[0]?.store_id ?? "")
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "加载失败")
        setLoading(false)
      })
  }, [])

  const headers = useMemo(() => {
    if (!context) return null
    return {
      "Content-Type": "application/json",
      "X-Tenant-Id": context.tenant_id,
      "X-Store-Id": context.store_id,
      "X-Approver-Id": approverId,
    }
  }, [approverId, context])

  const loadActions = useCallback(async () => {
    if (!headers) return
    try {
      const data = await fetchActions(filter, headers)
      setError("")
      setActions(data)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审批队列加载失败")
    } finally {
      setLoading(false)
    }
  }, [filter, headers])

  useEffect(() => {
    if (!headers) return
    let active = true
    void fetchActions(filter, headers)
      .then((data) => {
        if (!active) return
        setError("")
        setActions(data)
      })
      .catch((reason: unknown) => {
        if (!active) return
        setError(reason instanceof Error ? reason.message : "审批队列加载失败")
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [filter, headers])

  async function transition(action: PendingAction, decision: "approve" | "reject") {
    if (!headers) return
    let body: string | undefined
    if (decision === "reject") {
      const reason = window.prompt("请输入拒绝原因")
      if (!reason?.trim()) return
      body = JSON.stringify({ reason: reason.trim() })
    }
    setBusyAction(action.id)
    setError("")
    try {
      const response = await fetch(
        `${API_BASE}/api/v1/approvals/${action.id}/${decision}`,
        { method: "POST", headers, body },
      )
      if (!response.ok) {
        const payload = (await response.json()) as { detail?: string }
        throw new Error(payload.detail ?? `操作失败（${response.status}）`)
      }
      await loadActions()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败")
    } finally {
      setBusyAction(null)
    }
  }

  const pendingCount = actions.filter((action) => action.status === "pending").length

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">CA</div>
        <div className="brand-copy">
          <strong>Commerce Agent</strong>
          <span>Human approval console</span>
        </div>
        <a className="docs-link" href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">
          API 文档 ↗
        </a>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">MILESTONE 04 · CONTROL PLANE</p>
          <h1>人工审批队列</h1>
          <p className="hero-copy">
            Agent 只能提出退款、发券和取消订单请求。业务数据将在人工批准并重新校验状态后才会改变。
          </p>
        </div>
        <div className="hero-metric">
          <span>{filter === "pending" ? pendingCount : "—"}</span>
          <small>当前视图待处理</small>
        </div>
      </section>

      <section className="controls" aria-label="审批筛选">
        <label>
          <span>店铺</span>
          <select value={context?.store_id ?? ""} onChange={(event) => setSelectedStore(event.target.value)}>
            {contexts.map((item) => (
              <option key={item.store_id} value={item.store_id}>
                {item.tenant_name} / {item.store_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>审批人</span>
          <input value={approverId} onChange={(event) => setApproverId(event.target.value)} />
        </label>
        <div className="filter-tabs" aria-label="状态">
          {[
            ["pending", "待审批"],
            ["succeeded", "已完成"],
            ["rejected", "已拒绝"],
            ["failed", "失败"],
            ["all", "全部"],
          ].map(([value, label]) => (
            <button
              className={filter === value ? "active" : ""}
              key={value}
              onClick={() => setFilter(value)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <section className="queue" aria-live="polite">
        {loading ? (
          <div className="empty-state">正在读取审批队列…</div>
        ) : actions.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">✓</div>
            <h2>当前队列为空</h2>
            <p>通过 Agent 对话请求退款、发券或取消订单后，审批会出现在这里。</p>
          </div>
        ) : (
          actions.map((action) => (
            <article className="approval-card" key={action.id}>
              <div className="card-heading">
                <div>
                  <span className={`status status-${action.status}`}>{statusLabels[action.status]}</span>
                  <h2>{actionLabels[action.action_type]}</h2>
                  <p>{payloadSummary(action)}</p>
                </div>
                <time>{formatDate(action.created_at)}</time>
              </div>

              <dl className="action-details">
                <div>
                  <dt>审批编号</dt>
                  <dd>{action.id}</dd>
                </div>
                <div>
                  <dt>顾客</dt>
                  <dd>{action.customer_id}</dd>
                </div>
                <div>
                  <dt>原因</dt>
                  <dd>{String(action.payload.reason ?? "未提供")}</dd>
                </div>
              </dl>

              {(action.result || action.failure_code || action.rejection_reason) && (
                <div className="outcome">
                  {action.result && <code>{JSON.stringify(action.result)}</code>}
                  {action.failure_code && <span>失败原因：{action.failure_code}</span>}
                  {action.rejection_reason && <span>拒绝原因：{action.rejection_reason}</span>}
                </div>
              )}

              <details className="audit-trail">
                <summary>审计记录 · {action.audit_logs.length} 条</summary>
                <ol>
                  {action.audit_logs.map((event) => (
                    <li key={event.id}>
                      <span>{event.event_type}</span>
                      <small>{event.actor_id} · {formatDate(event.created_at)}</small>
                    </li>
                  ))}
                </ol>
              </details>

              {action.status === "pending" && (
                <div className="card-actions">
                  <button
                    className="reject"
                    disabled={busyAction === action.id}
                    onClick={() => void transition(action, "reject")}
                    type="button"
                  >
                    拒绝
                  </button>
                  <button
                    className="approve"
                    disabled={busyAction === action.id}
                    onClick={() => void transition(action, "approve")}
                    type="button"
                  >
                    {busyAction === action.id ? "处理中…" : "批准并执行"}
                  </button>
                </div>
              )}
            </article>
          ))
        )}
      </section>
    </main>
  )
}
