# Milestone 5 Review 后的可解释性改进

## 1. 为什么优先改这些问题

这轮没有把 Review 中所有生产化问题一次做完，而是按求职 Demo 的目标排序：面试官看到一条
失败结果时，系统必须能回答“执行是否真的跑完、错在哪一步、用了什么证据、为什么被判失败”。

因此优先处理四件事：

1. 失败请求原来会随业务事务一起回滚，Trace API 看不到 error timeline；
2. `started_at` 原来在最终 flush 时由数据库生成，时间线起点不准确；
3. RAG 评测原来只检查 citation 的字符串格式，引用错误知识也可能通过；
4. 评测 run 的“执行状态”和“回答质量”混在一起，空样本还会显示 100%。

API 管理员权限、顾客级 Trace 授权、后台评测队列、完整 PII 识别等问题很重要，但主要影响生产
稳定性和治理，这轮明确延后，避免 Demo 主线被基础设施工作淹没。

## 2. 失败 Trace 如何保证既可见又不误提交业务状态

失败路径不能直接 `commit` 当前 Session，因为工具可能已经创建了待审批动作或部分消息。直接
提交虽然保住 Trace，却也会把本应回滚的业务状态一起提交。

现在采用两阶段失败落库：

```text
运行中 Trace + 部分消息/业务写入
        -> 复制 Trace 和 event 的纯数据快照
        -> rollback 本次 turn
        -> 新事务只 INSERT failed Trace + error event
        -> 错误响应返回 trace_id
```

这样同时满足两个 Demo 目标：

- 不把失败执行产生的部分业务状态留在数据库；
- 面试演示时可以从错误响应中的 `trace_id` 打开 `request -> error` 时间线。

`AgentTrace.started_at` 也改为 Runtime 创建 Trace 时显式写入 UTC 时间，不再依赖结束时才触发的
数据库默认值。总延迟仍使用单调时钟计算，墙上时间用于展示，单调时间用于 duration，职责分开。

## 3. Citation 从“格式正确”升级为“证据正确”

数据集新增 `expected_citations`。例如退款到账用例不再只要求出现：

```text
[任意-source:v1#chunk-1]
```

而是明确要求：

```text
[refund-timing:v1#chunk-1]
```

Evaluator 将 citation 拆成三项：

- `citation_presence`：是否有版本化引用；
- `citation_correctness`：实际引用集合是否与用例期望集合一致，既不能缺少，也不能夹带错误切片；
- `citation`：前两项的聚合结论。

多主题用例也可以声明多个必需引用。例如“退货和换货规则分别是什么”必须同时命中
`quality-return` 和 `exchange-process`，避免只回答一半也通过。

每条 case result 新增 `evidence_json`，记录 expected/actual tools、expected/actual citations、缺失
citation、额外 citation、缺失必需文本和误出现的禁止文本。前端或 JSON 报告不必重新解析自然语言，就能直接
展示“期望什么、实际发生什么、差异在哪里”。数据库通过 0006 migration 增加该字段。因为判分
契约发生变化，dataset version 最终升为 `milestone-5-v3`，报告 schema 升为 `1.1`，避免把新旧分数
当作完全相同的基线比较。

评测页同步增加了中文指标名、run status/quality gate 双结论和 Citation 判分证据卡片。即使
60/60 全部通过，也能从页面直接对比 expected/actual citation，并沿 `trace_id` 跳到来源工具事件；
失败用例则可展开完整 `evidence_json`，不会只剩一个抽象的 `citation` 失败标签。

## 4. 区分执行状态、回答质量和无样本

评测 run 现在有两层结论：

- `run.status`：评测执行链路是否完整。任一 case 出现 provider/runtime 执行异常时为 `failed`；
- `metrics.quality_gate_passed`：工具、任务、citation、安全和延迟目标是否全部达标。

普通质量退化不会伪装成基础设施故障；反过来，全部 case 都因 provider 异常而没执行，也不会再
显示 `succeeded`。

新增 `execution_success_rate` 和 `citation_correctness`。指标没有适用样本时返回 JSON `null`，
对应 target 也为 `null`，不再用 100% 表示“没有数据”。这在演示子集评测时尤其重要。

聚合质量门禁只计算非 `null` 的适用目标，并在 `not_applicable_targets` 中列出没有样本的目标。
因此，一个只跑商品查询的子集不会因为“没有 citation 用例”被误判失败；如果没有任何适用目标，
聚合结果本身返回 `null`，而不是伪造通过或失败。

Citation 正确性采用期望集合与实际集合的一致性判断：`missing_citations` 解释缺了什么，
`unexpected_citations` 解释多了什么。集合比较忽略引用顺序和重复次数，但正确引用旁边夹带错误
切片仍会失败。前端证据卡片对应显示“一致/不一致”以及缺失、多余引用。

## 5. 如何演示

推荐用两个短场景说明：

### 场景 A：失败执行仍可解释

1. 让测试 Provider 抛出 `ModelProviderError`；
2. API 返回 502，同时给出 `trace_id`；
3. 查询 Trace，展示状态 `failed`、准确的 started/completed 时间，以及 `request -> error`；
4. 说明部分聊天消息和业务写入已 rollback，保留下来的只有不可变诊断证据。

### 场景 B：错误引用不能混过评测

1. 用例期望 `refund-timing:v1#chunk-1`；
2. 回复故意引用 `no-reason-return:v1#chunk-1`；
3. `citation_presence=true`，但 `citation_correctness=false`；
4. `evidence_json.missing_citations` 和 `unexpected_citations` 直接指出缺失及夹带的证据。

这两个场景比单纯展示 60/60 更能体现 Agent 工程思路：不仅给结果，还能给出可核验的执行证据
和失败归因。

## 6. 验证结果

- 容器门禁：Ruff、mypy strict、58 个 pytest 全部通过；
- Mock 完整评测：58/60，citation coverage 100%，citation correctness 83.33%；严格集合判分暴露出
  `knowledge_006` 与 `knowledge_007` 各夹带一个非期望切片，质量门禁如实未通过；
- 新增回归覆盖：失败 API 返回可查询 Trace、失败 evaluation run 关联 Trace、错误 citation 的
  存在性/正确性拆分、正确 citation 夹带错误 citation、子集无样本目标不误伤质量门禁；
- PostgreSQL Alembic：`20260815_0005 -> 20260815_0006` 成功应用。
- 浏览器验收：评测页正确显示“执行完成 / 质量门禁通过”、中文指标、`$0.00` 成本和
  expected/actual citation；“沿 Trace 查看来源”可跳转到完整 `request -> model -> tool -> model
  -> response` 时间线。

## 7. 明确延后的生产化事项

- Evaluation API 的管理员认证和 tenant/store scope；
- Trace API 的顾客级与运营级权限拆分；
- 真实模型多次采样 baseline；
- 多工具顺序/集合指标与更复杂的多轮数据集；
- 长耗时评测的后台队列、进度和取消；
- 更完整的 PII/DLP 与独立遥测基础设施。

面试时应主动说明这些边界：这不是“不知道生产还需要什么”，而是基于 Demo 目标做了有意识的
优先级取舍。
