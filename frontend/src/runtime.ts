export type RuntimeInfo = {
  provider: string
  model_name: string
  model_mode: string
  uses_external_api: boolean
  evaluation_case_count: number
  input_cost_per_million: string
  output_cost_per_million: string
}

export async function fetchRuntimeInfo(apiBase: string): Promise<RuntimeInfo> {
  const response = await fetch(`${apiBase}/api/v1/demo/runtime`)
  if (!response.ok) throw new Error(`运行时信息加载失败（${response.status}）`)
  return response.json() as Promise<RuntimeInfo>
}

export function runtimeLabel(runtime: RuntimeInfo | null): string {
  if (!runtime) return "正在读取模型配置…"
  const mode = runtime.model_mode === "non-thinking" ? "Non-thinking" : runtime.provider
  return `${runtime.model_name} · ${mode}`
}

export function evaluationConfirmation(runtime: RuntimeInfo): string {
  return [
    `即将使用 ${runtime.provider} / ${runtime.model_name} 运行 ${runtime.evaluation_case_count} 条用例。`,
    "这会调用外部模型 API 并产生实际用量。",
    `当前单价：输入 $${runtime.input_cost_per_million}/百万 tokens，输出 $${runtime.output_cost_per_million}/百万 tokens。`,
    "是否继续？",
  ].join("\n")
}

export function failedTraceUrl(
  traceId: string,
  scope: { tenantId: string; storeId: string },
): string {
  const params = new URLSearchParams({
    view: "traces",
    trace_id: traceId,
    tenant_id: scope.tenantId,
    store_id: scope.storeId,
  })
  return `/merchant?${params.toString()}`
}
