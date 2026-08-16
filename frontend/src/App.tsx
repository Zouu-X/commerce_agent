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

type TraceSummary = {
  id: string
  conversation_id: string
  customer_id: string
  status: "running" | "succeeded" | "failed"
  model_provider: string
  model_name: string
  prompt_version: string
  model_calls: number
  tool_calls: number
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: string
  first_model_response_ms: number | null
  total_latency_ms: number | null
  final_response_preview: string | null
  error_code: string | null
  started_at: string
  completed_at: string | null
}

type TraceEvent = {
  id: string
  event_index: number
  event_type: string
  name: string
  status: string
  input: Record<string, unknown> | null
  output: Record<string, unknown> | null
  latency_ms: number | null
  input_tokens: number | null
  output_tokens: number | null
  estimated_cost_usd: string | null
  created_at: string
}

type TraceDetail = TraceSummary & {
  tenant_id: string
  store_id: string
  events: TraceEvent[]
}

type EvaluationCase = {
  case_index: number
  case_id: string
  category: string
  trace_id: string | null
  trace_tenant_id: string | null
  trace_store_id: string | null
  input: string
  passed: boolean
  latency_ms: number
  failures: string[]
  actual_tools: string[]
  checks: Record<string, boolean>
  evidence: {
    expected_tools?: string[]
    actual_tools?: string[]
    expected_citations?: string[]
    actual_citations?: string[]
    missing_citations?: string[]
    unexpected_citations?: string[]
    missing_content?: string[]
    forbidden_content_found?: string[]
  }
}

type EvaluationRun = {
  id: string
  status: string
  dataset_name: string
  dataset_version: string
  provider: string
  model_name: string
  prompt_version: string
  total_cases: number
  passed_cases: number
  metrics: Record<string, unknown>
  started_at: string
  completed_at: string | null
  cases?: EvaluationCase[]
}

type ConsoleView = "approvals" | "traces" | "evaluations"

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

const metricLabels: Record<string, string> = {
  execution_success_rate: "执行成功率",
  tool_selection_accuracy: "工具选择准确率",
  necessary_tool_recall: "必要工具召回率",
  tool_parameter_validity: "参数有效率",
  task_completion_rate: "任务完成率",
  citation_coverage: "引用覆盖率",
  citation_correctness: "引用正确率",
  safety_pass_rate: "安全通过率",
  cross_scope_leakage_rate: "越权泄露率",
  unapproved_write_execution_rate: "未审批写入率",
  p95_latency_ms: "P95 延迟",
  total_estimated_cost_usd: "估算总成本",
}

const rateMetricNames = new Set([
  "execution_success_rate",
  "tool_selection_accuracy",
  "necessary_tool_recall",
  "tool_parameter_validity",
  "task_completion_rate",
  "citation_coverage",
  "citation_correctness",
  "safety_pass_rate",
  "cross_scope_leakage_rate",
  "unapproved_write_execution_rate",
])

function formatMetric(name: string, value: unknown) {
  if (value === null) return "无样本"
  if (name === "p95_latency_ms") return `${String(value)} ms`
  if (name === "total_estimated_cost_usd") {
    const amount = Number(value)
    return Number.isFinite(amount) ? `$${amount.toFixed(amount === 0 ? 2 : 8)}` : `$${String(value)}`
  }
  if (typeof value === "number" && rateMetricNames.has(name)) {
    return `${(Number(value) * 100).toFixed(1)}%`
  }
  return String(value)
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
  const [view, setView] = useState<ConsoleView>("approvals")
  const [contexts, setContexts] = useState<DemoContext[]>([])
  const [selectedStore, setSelectedStore] = useState("")
  const [filter, setFilter] = useState("pending")
  const [approverId, setApproverId] = useState("ops-reviewer@example.com")
  const [actions, setActions] = useState<PendingAction[]>([])
  const [loading, setLoading] = useState(true)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [error, setError] = useState("")
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [selectedTrace, setSelectedTrace] = useState<TraceDetail | null>(null)
  const [evaluationRuns, setEvaluationRuns] = useState<EvaluationRun[]>([])
  const [selectedRun, setSelectedRun] = useState<EvaluationRun | null>(null)
  const [runningEvaluation, setRunningEvaluation] = useState(false)

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

  const loadTraces = useCallback(async () => {
    if (!headers) return
    try {
      const response = await fetch(`${API_BASE}/api/v1/traces?limit=30`, { headers })
      if (!response.ok) throw new Error(`Trace 加载失败（${response.status}）`)
      setTraces((await response.json()) as TraceSummary[])
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Trace 加载失败")
    }
  }, [headers])

  const loadEvaluations = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/evaluations/runs`)
      if (!response.ok) throw new Error(`评测记录加载失败（${response.status}）`)
      setEvaluationRuns((await response.json()) as EvaluationRun[])
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "评测记录加载失败")
    }
  }, [])

  async function openTrace(traceId: string, traceScope?: { tenantId: string; storeId: string }) {
    if (!headers && !traceScope) return
    const traceHeaders = traceScope
      ? { "X-Tenant-Id": traceScope.tenantId, "X-Store-Id": traceScope.storeId }
      : headers!
    const response = await fetch(`${API_BASE}/api/v1/traces/${traceId}`, { headers: traceHeaders })
    if (!response.ok) {
      setError(`Trace 详情加载失败（${response.status}）`)
      return
    }
    setSelectedTrace((await response.json()) as TraceDetail)
  }

  async function openEvaluation(runId: string) {
    const response = await fetch(`${API_BASE}/api/v1/evaluations/runs/${runId}`)
    if (!response.ok) {
      setError(`评测详情加载失败（${response.status}）`)
      return
    }
    setSelectedRun((await response.json()) as EvaluationRun)
  }

  async function runEvaluation() {
    setRunningEvaluation(true)
    setError("")
    try {
      const response = await fetch(`${API_BASE}/api/v1/evaluations/runs`, { method: "POST" })
      if (!response.ok) throw new Error(`评测执行失败（${response.status}）`)
      const run = (await response.json()) as EvaluationRun
      setSelectedRun(run)
      await loadEvaluations()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "评测执行失败")
    } finally {
      setRunningEvaluation(false)
    }
  }

  function showTraceFromEvaluation(traceId: string, tenantId: string, storeId: string) {
    setView("traces")
    setSelectedRun(null)
    void openTrace(traceId, { tenantId, storeId })
  }

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
  const citationEvidenceCases = selectedRun?.cases
    ?.filter((item) => (item.evidence.expected_citations?.length ?? 0) > 0)
    .slice(0, 4) ?? []

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">CA</div>
        <div className="brand-copy">
          <strong>Commerce Agent</strong>
          <span>Human approval console</span>
        </div>
        <nav className="console-nav" aria-label="控制台视图">
          {[
            ["approvals", "审批"],
            ["traces", "Trace"],
            ["evaluations", "评测"],
          ].map(([value, label]) => (
            <button
              className={view === value ? "active" : ""}
              key={value}
              onClick={() => {
                setView(value as ConsoleView)
                setSelectedTrace(null)
                setSelectedRun(null)
                if (value === "traces") void loadTraces()
                if (value === "evaluations") void loadEvaluations()
              }}
              type="button"
            >
              {label}
            </button>
          ))}
        </nav>
        <a className="docs-link" href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">
          API 文档 ↗
        </a>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">MILESTONE 05 · EVALUATION & OBSERVABILITY</p>
          <h1>{view === "approvals" ? "人工审批队列" : view === "traces" ? "执行时间线" : "离线评测"}</h1>
          <p className="hero-copy">
            {view === "approvals"
              ? "Agent 只能提出退款、发券和取消订单请求。业务数据将在人工批准并重新校验状态后才会改变。"
              : view === "traces"
                ? "沿着一次 Agent turn 查看模型、工具、token、成本、延迟和最终结果。"
                : "用固定数据集量化工具选择、参数、任务完成、引用和安全边界。"}
          </p>
        </div>
        <div className="hero-metric">
          <span>{view === "approvals" ? (filter === "pending" ? pendingCount : "—") : view === "traces" ? traces.length : evaluationRuns.length}</span>
          <small>{view === "approvals" ? "当前视图待处理" : view === "traces" ? "最近 Trace" : "历史评测"}</small>
        </div>
      </section>

      {view === "approvals" && (
        <>
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
        </>
      )}

      {view === "traces" && (
        <section className="observability-grid">
          <div className="trace-list panel">
            <div className="panel-heading">
              <h2>最近执行</h2>
              <button onClick={() => void loadTraces()} type="button">刷新</button>
            </div>
            {traces.length === 0 ? <p className="muted">发送一条 Agent 消息后，Trace 会出现在这里。</p> : traces.map((trace) => (
              <button className={`trace-row ${selectedTrace?.id === trace.id ? "active" : ""}`} key={trace.id} onClick={() => void openTrace(trace.id)} type="button">
                <span><strong>{trace.status}</strong><small>{formatDate(trace.started_at)}</small></span>
                <span><strong>{trace.total_latency_ms ?? "—"} ms</strong><small>{trace.tool_calls} tools · {trace.model_calls} model</small></span>
              </button>
            ))}
          </div>
          <div className="timeline panel">
            {!selectedTrace ? <div className="empty-state compact"><h2>选择一个 Trace</h2><p>查看按事件序号稳定排序的完整执行链。</p></div> : (
              <>
                <div className="trace-summary">
                  <div><span>Trace ID</span><code>{selectedTrace.id}</code></div>
                  <div><span>Token</span><strong>{selectedTrace.input_tokens + selectedTrace.output_tokens}</strong></div>
                  <div><span>估算成本</span><strong>${selectedTrace.estimated_cost_usd}</strong></div>
                  <div><span>首响应</span><strong>{selectedTrace.first_model_response_ms ?? "—"} ms</strong></div>
                </div>
                <ol className="event-timeline">
                  {selectedTrace.events.map((event) => (
                    <li key={event.id}>
                      <span className={`event-dot event-${event.status}`} />
                      <div><small>#{event.event_index} · {event.event_type}</small><h3>{event.name}</h3><p>{event.latency_ms === null ? "即时事件" : `${event.latency_ms} ms`}</p><details><summary>结构化数据</summary><code>{JSON.stringify({ input: event.input, output: event.output }, null, 2)}</code></details></div>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </div>
        </section>
      )}

      {view === "evaluations" && (
        <section className="evaluation-layout">
          <div className="evaluation-toolbar panel">
            <div><h2>评测运行</h2><p className="muted">固定 60 条用例，结果按 dataset / prompt / model 版本保存。</p></div>
            <button className="approve" disabled={runningEvaluation} onClick={() => void runEvaluation()} type="button">{runningEvaluation ? "正在运行 60 条用例…" : "运行完整评测"}</button>
          </div>
          <div className="run-list">
            {evaluationRuns.map((run) => (
              <button className="run-card" key={run.id} onClick={() => void openEvaluation(run.id)} type="button"><span><strong>{run.dataset_version}</strong><small>{formatDate(run.started_at)} · {run.model_name}</small></span><b>{run.passed_cases}/{run.total_cases}</b></button>
            ))}
          </div>
          {selectedRun && (
            <div className="evaluation-detail panel">
              <div className="evaluation-outcome">
                <span className={`run-status run-status-${selectedRun.status}`}>{selectedRun.status === "succeeded" ? "执行完成" : selectedRun.status === "failed" ? "执行失败" : "运行中"}</span>
                <div>
                  <strong>{selectedRun.metrics.quality_gate_passed === true ? "质量门禁通过" : selectedRun.metrics.quality_gate_passed === false ? "质量门禁未通过" : "质量门禁无适用指标"}</strong>
                  <small>执行状态与回答质量分开判定 · {selectedRun.dataset_version} / {selectedRun.prompt_version}</small>
                </div>
              </div>
              <div className="metric-grid">
                {Object.entries(metricLabels).filter(([name]) => name in selectedRun.metrics).map(([name, label]) => <div key={name}><span>{label}</span><strong>{formatMetric(name, selectedRun.metrics[name])}</strong></div>)}
              </div>
              <h2>失败用例</h2>
              <div className="case-list">
                {selectedRun.cases?.filter((item) => !item.passed).map((item) => <article key={item.case_id}><div><strong>{item.case_id}</strong><p>{item.input}</p><small>{item.failures.join(" · ")} · tools: {item.actual_tools.join(", ") || "none"}</small><details><summary>查看判分证据</summary><code>{JSON.stringify(item.evidence, null, 2)}</code></details></div>{item.trace_id && item.trace_tenant_id && item.trace_store_id && <button onClick={() => item.trace_id && item.trace_tenant_id && item.trace_store_id && showTraceFromEvaluation(item.trace_id, item.trace_tenant_id, item.trace_store_id)} type="button">查看 Trace</button>}</article>)}
                {selectedRun.cases?.every((item) => item.passed) && <p className="muted">全部用例通过。</p>}
              </div>
              <h2>Citation 判分证据</h2>
              <p className="muted">同时展示“是否引用”与“是否引用了正确切片”，避免格式正确但证据错误。</p>
              <div className="evidence-grid">
                {citationEvidenceCases.map((item) => (
                  <article className="evidence-card" key={item.case_id}>
                    <div className="evidence-heading"><strong>{item.case_id}</strong><span>{item.checks.citation_correctness ? "一致" : "不一致"}</span></div>
                    <p>{item.input}</p>
                    <dl>
                      <div><dt>期望</dt><dd>{item.evidence.expected_citations?.join("\n")}</dd></div>
                      <div><dt>实际</dt><dd>{item.evidence.actual_citations?.join("\n") || "无"}</dd></div>
                      {!!item.evidence.missing_citations?.length && <div><dt>缺失</dt><dd>{item.evidence.missing_citations.join("\n")}</dd></div>}
                      {!!item.evidence.unexpected_citations?.length && <div><dt>多余</dt><dd>{item.evidence.unexpected_citations.join("\n")}</dd></div>}
                    </dl>
                    {item.trace_id && item.trace_tenant_id && item.trace_store_id && <button onClick={() => item.trace_id && item.trace_tenant_id && item.trace_store_id && showTraceFromEvaluation(item.trace_id, item.trace_tenant_id, item.trace_store_id)} type="button">沿 Trace 查看来源</button>}
                  </article>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </main>
  )
}
