# Milestone 5：离线评测与全链路可观测性

## 1. 这一阶段解决什么问题

前四个 Milestone 已经让 Agent 能查询真实业务数据、检索版本化知识、创建待审批写操作，并在
审批后安全执行。但“Demo 看起来能工作”还不能回答以下问题：

- 改 Prompt 或换模型后，工具选择到底变好还是变差？
- 参数错误、引用缺失、越权泄露和绕过审批能否被自动发现？
- 一次回答为什么失败，问题发生在模型、工具还是业务服务？
- 单次请求用了多少 token、耗时多少、预计花费多少？
- 面试官能否用一条命令复现同一套测试并拿到机器可读报告？

Milestone 5 的目标就是把项目从“功能型 Demo”升级为“可度量、可回归、可解释的 Agent
工程系统”。实现分成两条链路，但两者共用同一份事实数据：

```text
在线请求 -> Agent Runtime -> Trace / Trace Events -> Trace API / 控制台
离线用例 -> 同一个 Agent Runtime -> 同一套 Trace -> Evaluator -> 报告 / 控制台
```

离线评测没有绕过 Runtime 直接调用 Provider，也没有为测试复制一套工具逻辑。这一点很重要：
评测覆盖的是实际运行路径，而不是一个与生产代码逐渐漂移的“假 Agent”。

## 2. 数据模型为什么这样设计

新增四张表：

### 2.1 `agent_traces`

一行代表一次完整 Agent turn，保存汇总信息：

- tenant、store、customer、conversation 和 trace ID；
- `running / succeeded / failed` 状态；
- Provider、模型名和 Prompt 版本；
- model/tool 调用次数；
- 输入、输出 token；
- 首个模型响应时间、总延迟；
- 估算成本、最终回复预览和错误码。

Trace 直接绑定可信业务上下文。查询 API 必须同时匹配 tenant 和 store，不能只靠用户提交的
trace ID 读取其他店铺的执行内容。

### 2.2 `trace_events`

一行代表 Trace 中的一个事件，事件类型包括：

- `request`：收到用户消息；
- `model`：一次模型调用完成；
- `tool`：一次工具调用完成；
- `response`：最终回复；
- `error`：运行失败。

每个事件带结构化 input/output、状态、延迟、token 和成本。事件使用
`(trace_id, event_index)` 唯一约束，并按 `event_index` 排序。

为什么不能只按 `created_at` 排序：一次本地 Mock 调用可能在同一毫秒内完成多个事件，数据库
时间戳可能完全相同。显式序号既保证 UI 时间线稳定，也让测试可以断言准确执行顺序。

### 2.3 `evaluation_runs`

一行代表一次完整评测，保存：

- dataset 名称和版本；
- Provider、模型和 Prompt 版本；
- 总用例数、通过数和结构化指标；
- 开始、完成时间和运行状态。

这些版本字段是做 A/B 对比的基础。只有数据集、Prompt 和模型身份都被记录，两个百分比才有
可比性。

### 2.4 `evaluation_case_results`

一行代表一条用例结果，保存：

- case ID、分类、输入；
- 是否通过、实际工具列表；
- 每项检查结果和失败原因；
- 期望/实际工具、期望/实际 citation、缺失内容等结构化证据；
- 延迟、成本、回复预览；
- 对应的 `trace_id`。

只保存总分会让评测失去诊断价值。逐例结果可以回答“哪类问题退化了”，`trace_id` 又能继续
回答“这条具体在哪一步错了”。

## 3. Runtime 埋点是怎么接入的

`AgentRuntime` 在每个 turn 开始时创建 Trace，并在原执行循环的自然边界记录事件：

```text
request_received
  -> model_completed(loop=1)
  -> tool_completed(...)
  -> model_completed(loop=2)
  -> assistant_response
```

模型调用使用单调时钟计算耗时，避免系统时间调整影响 duration。Runtime 累加 Provider 返回的
usage，并在 Trace 中保存 input/output token。Mock Provider 不产生 token，因此值为 0；真实
OpenAI-compatible Provider 会使用响应中的 `prompt_tokens` 和 `completion_tokens`。

成本按可配置单价计算：

```text
cost = input_tokens * input_price_per_million / 1,000,000
     + output_tokens * output_price_per_million / 1,000,000
```

结果使用 Decimal 保存到 8 位小数，避免用二进制浮点处理货币。单价通过：

- `MODEL_INPUT_COST_PER_MILLION`
- `MODEL_OUTPUT_COST_PER_MILLION`

配置。Mock 默认单价为 0，因为把一个随时间变化的真实模型价格硬编码进 Demo 会产生误导。

当前 Provider 是非流式 Chat Completions 适配器，所以项目记录的是“首个完整模型响应时间”而
不声称是 streaming TTFT。未来接入流式 Provider 后，可以单独增加首 token 事件。

## 4. Trace 中的数据安全

可观测性系统本身也可能成为数据泄露入口，因此事件写入前会递归脱敏：

- authorization、API key、password 和 token；
- email、phone、address、recipient；
- tracking number；
- 文本中的邮箱地址和中国大陆手机号模式。

长字符串会截断，Trace API 又要求 tenant/store 匹配。完整聊天仍由 Conversation 负责，Trace
只保存用于诊断的有限预览，避免无边界复制业务数据。

## 5. 60 条评测集是怎么组织的

数据集位于 `backend/app/evaluations/data/milestone5.jsonl`。初始版本为 `milestone-5-v1`；加入
citation correctness 判分后升级为 `milestone-5-v2`；进一步把“夹带非期望引用”纳入判分并修正
无样本质量门禁语义后升级为 `milestone-5-v3`。

分类分布：

| 分类 | 数量 | 主要覆盖 |
|---|---:|---|
| 商品 | 10 | 关键词、分类、库存、两个店铺 |
| 订单 | 10 | 列表、详情、状态、跨顾客/跨 tenant |
| 物流 | 8 | 正常、5 天未更新、配送失败、越权 |
| 售后 | 4 | 正常状态、跨顾客/跨 tenant |
| 知识 | 12 | 退货、发货、保价、补偿、质保、无证据回退 |
| 写操作 | 8 | 取消、发券、退款、归属检查、待审批 |
| 安全 | 8 | 身份覆盖、审批绕过、Prompt Injection、数据外带 |
| 合计 | 60 | 功能、RAG、工具和安全边界 |

每条用例可以声明：

- `expected_tools` 和 `forbidden_tools`；
- 允许的业务错误码，例如越权统一表现为 not found；
- 回复必须包含/不得包含的文本；
- 是否必须带 citation；
- 写操作是否必须停留在 pending；
- cross-tenant、cross-customer、prompt-injection 等标签。

数据集启动时校验条数必须在 50～100 之间，并拒绝重复 case ID。售后 UUID 使用固定 seed key
渲染，既保持 JSONL 可读，又保证每次 reset 后输入一致。

## 6. Evaluator 如何判分

一条用例运行完成后，Evaluator 从 Trace 的 tool event 提取真实工具和结构化结果，而不是从
回复文本猜“模型可能调用了什么”。每条用例有七组独立检查：

1. `tool_selection`：实际工具序列是否等于期望序列；
2. `parameter_validity`：是否出现 invalid arguments 或 unknown tool；
3. `task_completion`：工具结果是否成功或命中允许错误，回复内容约束是否满足；
4. `citation_presence`：需要知识证据时，回复是否带版本化 citation；
5. `citation_correctness`：实际 citation 集合是否与用例期望集合一致；不仅检查缺失，也拒绝在正确
   引用旁夹带非期望切片；
6. `citation`：存在性与正确性的聚合结果；
7. `safety`：禁止工具没有调用、敏感内容没有出现、写操作没有被直接执行，并在要求时保持
   pending。

汇总指标包括：

- 工具选择准确率；
- 必要工具召回率；
- 工具参数有效率；
- 任务完成率；
- 引用覆盖率；
- 引用正确率；
- 执行成功率与整体质量门禁；
- 安全通过率；
- 跨范围泄露率；
- 未审批写操作执行率；
- P95 延迟；
- 总估算成本。

报告里还保存每项目标是否达到。没有适用样本的目标为 `null` 并列入
`not_applicable_targets`；聚合质量门禁只计算适用目标。CI 或后续脚本可以直接读取这些布尔值
作为质量门禁。

## 7. 评测如何反向驱动修复

第一次真实 PostgreSQL 运行结果是 55/60，虽然已经达到计划中的全部 MVP 门槛，但没有把失败
简单归因成“模型不稳定”。通过失败用例的 trace 定位到两类确定性问题：

1. Mock Provider 没把“运动分类有什么现货”识别成商品意图，也没把“配送怎么样”识别成物流
   意图；补充的是通用中文意图词，不是对 case ID 写特判。
2. 物流越权服务统一返回 `shipment_not_found`，数据集却只允许 `order_not_found`。两者对用户都
   渲染成“当前账号下未找到”，修正了评测契约中的实际服务错误码。

按 v2 契约修复后，同一条 `make eval` 在真实 PostgreSQL 上得到 60/60。随后 v3 把“正确引用
旁夹带非期望切片”也判为失败，基线变为 58/60。这不是回归链路坏了，而是更严格的判分揭示了
`knowledge_006` 和 `knowledge_007` 的证据选择噪声。这个过程展示了评测的核心价值：既能找到
实现缺陷，也能找到评测本身的错误假设，收紧契约后还会如实暴露新的质量差距。

## 8. 一条命令和结构化报告

运行：

```bash
make eval
```

命令会：

1. 构建最新 API 镜像；
2. 等待 PostgreSQL；
3. 执行 Alembic 到 head；
4. reset 确定性 Demo 数据；
5. 运行 60 条真实 Agent 链路；
6. 保存 Trace 证据并清理评测创建的 pending action，避免污染人工审批队列；
7. 把结果写入数据库；
8. 生成 `eval-results/evaluation-<run_id>.json` 和 `latest.json`。

每次用 run ID 生成独立文件，方便保留多个版本比较；`latest.json` 方便 CI 或本地脚本找到最近
结果。JSON 包含 schema version，后续扩展字段时可以维护消费者兼容性。

## 9. API 与控制台

Trace API：

- `GET /api/v1/traces`
- `GET /api/v1/traces/{trace_id}`

Evaluation API：

- `POST /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs/{run_id}`

前端从单一审批页扩展成三个视图：

- 审批：沿用 Milestone 4 的人工审批队列；
- Trace：查看每次执行的 token、成本、延迟和事件时间线；
- 评测：运行/查看历史评测、指标和失败用例。

失败用例可以直接跳到对应 Trace，形成“指标发现问题 -> case 定位范围 -> trace 定位步骤”的
调试闭环。

## 10. 真实验证结果

2026-08-15 使用 Docker PostgreSQL 17 + pgvector 和 Mock Provider：

| 指标 | 结果 |
|---|---:|
| 用例 | 58 / 60 |
| 工具选择准确率 | 100% |
| 必要工具召回率 | 100% |
| 参数有效率 | 100% |
| 任务完成率 | 100% |
| 引用覆盖率 | 100% |
| 引用正确率 | 83.33% |
| 安全通过率 | 100% |
| 跨范围泄露率 | 0% |
| 未审批写执行率 | 0% |
| P95 延迟 | 11 ms |
| Mock 估算成本 | $0 |

自动检查包括：

- Ruff；
- mypy strict；
- 58 个 pytest 测试；
- 前端 ESLint；
- 前端 TypeScript + Vite production build；
- Alembic PostgreSQL 迁移和 schema check；
- 真实 PostgreSQL 60 条一键评测；
- Trace tenant/store 隔离和稳定事件顺序测试；
- 脱敏与 Decimal 成本公式测试。

## 11. 如何在面试中讲这一阶段

可以按下面的顺序讲：

> 前四个阶段证明了 Agent 能完成任务，Milestone 5 解决的是怎么证明它持续做对。我没有写一套
> 只调用 Mock 的评测逻辑，而是让 60 条版本化用例走和在线请求完全相同的 Runtime、工具、服务
> 和数据库链路。Runtime 会把请求、每次模型和工具调用、回复记录成稳定排序的 Trace，同时汇总
> token、可配置成本和延迟。Evaluator 从结构化 tool event 计算工具选择、参数、任务、引用和
> 安全指标，每条失败都关联 trace ID。第一次 PostgreSQL 实跑是 55/60，我通过 trace 找到两个
> 中文意图词覆盖和三个错误码契约问题，按 v2 修正后达到 60/60。之后我没有停在漂亮分数上，
> 而是把 citation correctness 收紧为引用集合一致性；v3 因两条回答夹带额外切片降到 58/60，
> 页面会直接展示多余证据并关联 Trace。这是确定性 Mock 的回归基线，不是对真实模型泛化能力的
> 宣传；换真实 Provider 时仍使用相同数据集，并按模型、Prompt 和数据集版本做可比报告。

## 12. 设计取舍和已知限制

### 为什么用 JSONL

每行一条用例，Git diff 清楚，合并冲突小，也能流式读取。数据量扩大后可以按领域拆分多个文件，
loader 和 evaluator 不需要改变。

### 为什么 API 当前同步运行评测

60 条 Mock 用例不到一秒，同步实现更容易演示和测试。真实模型评测可能持续数分钟并产生费用，
生产版本应改成受权限保护的后台队列，提供取消、并发限制和进度事件。

### 为什么 Mock 高分不能代表线上效果

Mock 是确定性路由器，目标是给业务、RAG、安全和可观测链路提供稳定回归基线。真实模型需要
增加同义改写、长对话、多轮澄清和人工标注集，并运行多次统计均值和方差。

### Trace 与业务事务

成功请求的消息、工具结果和 Trace 仍在同一个事务中原子提交。失败时先复制内存中的 Trace
证据，回滚本次 turn 产生的部分消息和业务写入，再在干净事务中只持久化失败 Trace 与 error
event。错误响应会返回 `trace_id`，因此 Demo 可以从一次 502/504/limit 错误直接跳到失败时间线。
生产环境若要跨数据库故障保留遥测，仍应使用 OpenTelemetry collector 等独立通道。

### 下一阶段

Milestone 6 将完善架构图、威胁模型、3～5 分钟演示脚本、设计取舍说明和公开部署方式，让没有
项目背景的评审者能在 10 分钟内启动并理解完整系统。
