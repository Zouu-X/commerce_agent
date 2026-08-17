import assert from "node:assert/strict"
import test from "node:test"

import {
  evaluationConfirmation,
  failedTraceUrl,
  runtimeLabel,
} from "../src/runtime.ts"

const deepSeekRuntime = {
  provider: "deepseek",
  model_name: "deepseek-v4-flash",
  model_mode: "non-thinking",
  uses_external_api: true,
  evaluation_case_count: 60,
  input_cost_per_million: "0.14",
  output_cost_per_million: "0.28",
}

test("external evaluation confirmation identifies model, scale, usage and price", () => {
  const message = evaluationConfirmation(deepSeekRuntime)

  assert.match(message, /deepseek \/ deepseek-v4-flash/)
  assert.match(message, /60 条用例/)
  assert.match(message, /产生实际用量/)
  assert.match(message, /输入 \$0\.14/)
  assert.match(message, /输出 \$0\.28/)
})

test("runtime label reflects the configured model instead of hardcoding DeepSeek", () => {
  assert.equal(runtimeLabel(deepSeekRuntime), "deepseek-v4-flash · Non-thinking")
  assert.equal(runtimeLabel({ ...deepSeekRuntime, provider: "mock", model_name: "mock-agent", model_mode: "provider-default" }), "mock-agent · mock")
})

test("failed trace link carries the trusted tenant and store scope", () => {
  assert.equal(
    failedTraceUrl("trace/id", { tenantId: "tenant one", storeId: "store&two" }),
    "/merchant?view=traces&trace_id=trace%2Fid&tenant_id=tenant+one&store_id=store%26two",
  )
})
