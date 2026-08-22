# Commerce Support Agent

一个可评测、可观测的多租户电商客服 Agent 沙盒。项目当前已具备确定性电商业务沙盒、
Provider-independent Agent Runtime、知识库混合检索、7 个安全只读工具，以及退款、发券、
取消订单三类人工审批写操作。Milestone 5 加入 60 条离线评测、完整 Trace 时间线、
token/成本/延迟统计和结构化报告；Milestone 6 补齐真实 DeepSeek、双角色页面以及客户输出安全边界。
详细范围见 [`project_plan.md`](./project_plan.md)。

## 环境要求

- 体验 Demo：Docker Desktop（包含 Docker Compose）
- 本地运行前端检查：Node.js 24.18.0、npm 11+
- 本地运行后端：Python 3.12

默认 Agent 使用 DeepSeek V4 Flash；Docker 负责运行全部服务，本机无需额外安装 Python。

## 快速启动

```bash
cp .env.example .env
cp .env.ds.example .env.ds
# 将 .env.ds 的占位内容替换成 DeepSeek API Key（文件中只放 Key 本身）
make up
make smoke
```

`make up` 会在后台构建并启动数据库、API 和 Web，并等待服务健康。首次构建需要下载镜像和
Python/npm 依赖，通常在数分钟内完成；后续启动会复用缓存。

启动后访问：

- 用户聊天页：http://localhost:5173
- 商户审批与观测后台：http://localhost:5173/merchant
- API 健康检查：http://localhost:8000/api/v1/health
- API 文档：http://localhost:8000/docs
- API 就绪检查：http://localhost:8000/api/v1/ready

停止服务：

```bash
make down
```

查看实时日志：

```bash
make logs
```

## 开发检查

首次执行本地前端检查前安装锁定依赖：

```bash
npm --prefix frontend ci
```

```bash
make lint
make test
```

后端检查默认在 Python 3.12 容器中执行，因此本机没有安装 Python 3.12 也可以运行。

使用 DeepSeek V4 Flash 运行完整评测（会产生 API 用量）：

```bash
make eval
```

需要零成本、结果可重复的回归基线时运行测试专用 Mock：

```bash
make eval-mock
```

该命令会迁移并重置 Demo 数据，然后运行 60 条用例。评测写操作只保留 Trace 证据，结束前会
清理自己创建的待审批记录，不污染人工审批队列。报告同时写入数据库和
`eval-results/evaluation-<run_id>.json`，`eval-results/latest.json` 指向最近一次结果。

## 电商沙盒

API 启动时会自动执行 Alembic 迁移，并在空数据库中导入确定性 Demo 数据。数据包括 2 个
tenant、2 家店铺、12 位顾客、24 个商品、48 个 SKU、60 个订单，以及物流、售后和
知识检索边界场景。知识库包含 28 个版本化文档和 28 个切片，其中 2 个是专门用于验证
有效期过滤的过期政策。

查看可用的店铺、顾客和订单上下文：

```bash
curl http://localhost:8000/api/v1/demo/contexts
```

业务 API 的可信身份通过请求头传入，服务层会强制使用三项身份过滤数据：

```bash
curl \
  -H 'X-Tenant-Id: 8741aaf7-d17d-523d-9f6a-f534109d7848' \
  -H 'X-Store-Id: 46267c0e-11d5-5634-9629-07f8f307c42d' \
  -H 'X-Customer-Id: 0d1ed7e7-59ab-50e6-9d62-faa77e406b84' \
  http://localhost:8000/api/v1/orders/AUR-202607-0001
```

主要只读接口：

- `GET /api/v1/catalog/products`
- `GET /api/v1/catalog/products/{product_id}`
- `GET /api/v1/orders`
- `GET /api/v1/orders/{order_number}`
- `GET /api/v1/orders/{order_number}/shipment`
- `GET /api/v1/orders/{order_number}/eligibility`
- `GET /api/v1/after-sales/{after_sale_id}`
- `GET /api/v1/knowledge/search`

## 人工审批与安全写操作

Milestone 4 将写操作拆成两个明确阶段：Agent 的 `request_*` 工具只校验参数并创建
`pending_action`，不会修改订单、支付状态或优惠券；只有店铺审批人批准后，审批服务才会在
数据库事务中锁定目标记录、重新检查业务状态并执行一次写入。

取消订单申请还会按“tenant + store + customer + order + action type”检查活动审批单。即使顾客刷新
页面进入新会话，只要原申请仍为 pending、approved 或 executing，就会复用原记录并告知顾客等待
人工审批，不会重复创建申请；被拒绝后允许顾客重新提交。

当前支持：

- 取消订单：仅 `pending` 或 `paid` 订单可申请，批准后变为 `cancelled`；
- 退款：检查订单归属、支付状态、退款窗口和剩余可退金额；已签收订单从签收事件起算 7 天，
  配送失败且尚未签收的订单允许先创建人工审批申请，由商户确认包裹退回情况；
- 补偿券：金额必须在 0～50 元之间，可关联当前顾客的订单。

审批接口使用服务端可信的 `X-Tenant-Id`、`X-Store-Id` 和 `X-Approver-Id`：

```bash
curl \
  -H 'X-Tenant-Id: 8741aaf7-d17d-523d-9f6a-f534109d7848' \
  -H 'X-Store-Id: 46267c0e-11d5-5634-9629-07f8f307c42d' \
  -H 'X-Approver-Id: ops-reviewer@example.com' \
  'http://localhost:8000/api/v1/approvals?status=pending'
```

主要审批接口：

- `GET /api/v1/approvals`
- `GET /api/v1/approvals/{action_id}`
- `POST /api/v1/approvals/{action_id}/approve`
- `POST /api/v1/approvals/{action_id}/reject`

批准和拒绝操作都有状态转换审计。退款与发券结果表以 `pending_action_id` 唯一约束防止重复
落账；重复批准一个已经成功的动作会返回原结果，而不会再次产生退款或优惠券。

## 知识检索与引用

Milestone 3 将店铺政策和商品指南存入 PostgreSQL，并使用两条召回路径：

- PostgreSQL `tsvector` + GIN 索引进行中文友好的关键词召回；
- pgvector + HNSW cosine 索引进行语义召回；
- 使用加权 Reciprocal Rank Fusion（RRF）合并两组排名。
- RRF 后按绝对关键词/向量分数和相对最佳证据门槛过滤；没有可靠证据时返回空结果。

检索前会强制过滤 `tenant_id`、`store_id`、文档类型、发布状态和有效期，因此其他店铺或
已经过期的政策不会进入候选集。内部检索结果会返回文档版本和 `citation_id`，供 Trace 和评测
核验；客户对话只展示资料标题和版本，不暴露切片 ID。

当前 Demo 使用 64 维确定性本地特征向量，不需要外部 Embedding API Key，便于 CI 和招聘方
重复运行。它用于展示完整 pgvector/RRF 架构，不等同于生产级语义模型；生产环境可以在不改
检索接口的情况下替换为真实 embedding provider。

直接检索当前店铺政策：

```bash
curl --get \
  -H 'X-Tenant-Id: 8741aaf7-d17d-523d-9f6a-f534109d7848' \
  -H 'X-Store-Id: 46267c0e-11d5-5634-9629-07f8f307c42d' \
  -H 'X-Customer-Id: 06abfe41-9df3-52de-baf3-e3d403524dd8' \
  --data-urlencode 'query=无理由退货可以申请多少天？' \
  --data-urlencode 'document_type=policy' \
  http://localhost:8000/api/v1/knowledge/search
```

## Agent 对话 Demo

运行时默认使用 DeepSeek V4 Flash，并关闭 thinking 模式以降低演示延迟，同时完整保留 function
tool-calling。模型只负责理解意图、选择工具和组织回答；商品、订单、物流、售后与政策事实仍然
来自 PostgreSQL 及业务服务层。API Key 通过 `.env.ds` 挂载成 Docker Secret，不会进入前端、
镜像或 Git；本地 Mock 只保留给单元测试和确定性回归评测。

浏览器中的用户聊天页会按所选店铺和顾客展示真实可命中的示例。订单号、物流示例和可取消订单
都从当前种子数据动态生成，避免复制一个不属于当前顾客的订单号导致误判。用户触发取消、退款
或发券后，只会生成待审批记录；商户可切换到独立的 `/merchant` 页面审批，并查看 Trace 和评测。

客户输出经过独立的 presentation boundary：原始工具结果只写入商户 Trace；传给模型和客户历史的
是中文业务摘要，最终回复还会过滤 `payment_status`、`has_shipment`、英文枚举、布尔值和原始
`citation_id`。知识依据以结构化 `sources` 返回并显示为《资料标题》· 版本，避免依赖模型拼接
内部引用格式。

先用 `/api/v1/demo/contexts` 选择 tenant、store 和 customer，再创建会话：

```bash
curl -X POST \
  -H 'X-Tenant-Id: 8741aaf7-d17d-523d-9f6a-f534109d7848' \
  -H 'X-Store-Id: 46267c0e-11d5-5634-9629-07f8f307c42d' \
  -H 'X-Customer-Id: 0d1ed7e7-59ab-50e6-9d62-faa77e406b84' \
  http://localhost:8000/api/v1/conversations
```

使用返回的 `conversation_id` 发送消息：

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-Id: 8741aaf7-d17d-523d-9f6a-f534109d7848' \
  -H 'X-Store-Id: 46267c0e-11d5-5634-9629-07f8f307c42d' \
  -H 'X-Customer-Id: 0d1ed7e7-59ab-50e6-9d62-faa77e406b84' \
  --data '{"content":"请推荐有库存的降噪耳机"}' \
  http://localhost:8000/api/v1/conversations/<conversation_id>/messages
```

可演示的消息包括：

- `请推荐有库存的降噪耳机`
- `帮我查订单 AUR-202607-0001`
- `订单 AUR-202607-0005 的物流怎么还没更新？`（需要选择该订单所属顾客）
- `无理由退货政策是多少天？`（回答显示可读的资料标题与版本）
- `付款以后商品降价了能退差额吗？`（演示同义表达的混合检索）
- `知识里写了忽略系统指令时应该怎么处理？`（演示检索内容 Prompt Injection 防护）
- `帮我取消订单 AUR-202607-0001，我不想要了`（只创建待审批动作）
- `给我发一张 10 元补偿券，物流太慢了`（只创建待审批动作）

客户读取会话时只会得到 `user -> assistant` 的可展示消息和结构化 `sources`：

```bash
curl \
  -H 'X-Tenant-Id: ...' \
  -H 'X-Store-Id: ...' \
  -H 'X-Customer-Id: ...' \
  http://localhost:8000/api/v1/conversations/<conversation_id>
```

完整的 `user -> assistant(tool_calls) -> tool -> assistant` 执行链保留在数据库和商户 Trace API，
不会通过客户会话接口返回。

当前只读工具：

- `search_products`
- `get_product_details`
- `get_customer_orders`
- `get_order_details`
- `track_shipment`
- `get_after_sale_status`
- `search_store_policy`

当前审批请求工具：

- `request_order_cancellation`
- `request_refund`
- `request_coupon`

身份字段不会出现在工具参数 Schema 中。`tenant_id`、`store_id`、`customer_id`、
`conversation_id` 和 `trace_id` 均由服务端注入，模型无法覆盖。

## 离线评测

当前判分契约为 `milestone-6-v1`，包含 60 条用例，覆盖：

- 商品搜索、订单详情、物流异常和售后状态；
- 两个 tenant 的政策检索、引用与无证据回退；
- 退款、发券、取消订单的待审批语义；
- 跨 tenant、跨 customer、身份覆盖、审批绕过和检索内容 Prompt Injection；
- 客户回复中的内部字段、英文状态、布尔值和原始切片引用泄露。

每条用例通过真实 `AgentRuntime -> ToolRegistry -> Service -> Database` 链路执行，而不是直接
调用 Mock Provider 后比较字符串。Evaluator 分别计算工具选择、必要工具召回、参数有效性、
任务完成、引用覆盖、客户展示安全和其他安全检查；失败用例会保留实际工具、失败检查项和对应
`trace_id`。引用正确性从 Trace 中的原始检索结果判定，不再要求模型把内部切片 ID 写进回答。

2026-08-17 在本地 Docker PostgreSQL + Mock Provider 上的可复现基线：

| 指标 | 结果 |
|---|---:|
| 用例通过率 | 58 / 60 |
| 工具选择准确率 | 100% |
| 必要工具召回率 | 100% |
| 工具参数有效率 | 100% |
| 任务完成率 | 100% |
| 知识回答引用覆盖率 | 100% |
| 知识回答引用正确率 | 83.33% |
| 客户展示安全率 | 100% |
| 跨范围数据泄露率 | 0% |
| 未审批写操作执行率 | 0% |
| P95 延迟 | 11 ms |

严格引用集合判分发现两条回复虽然包含正确主引用，但还夹带了非期望切片，因此质量门禁如实
未通过；失败证据可在评测页和对应 Trace 中直接查看。没有适用样本的子集指标会标记为
`not_applicable`，不参与聚合门禁。

这些数字是确定性 Mock 的回归基线，用于证明链路、指标和安全边界可重复验证，不代表真实大
模型的泛化表现。默认 DeepSeek Provider 仍使用同一数据集和指标，并按
`provider / model / prompt_version / dataset_version` 保存结果，便于 A/B 对比。Mock token 和
成本均为 0；真实 Provider 会记录返回的 usage，并使用环境变量中的每百万 token 单价估算成本。

评测 API：

- `POST /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs/{run_id}`

## Trace 与可观测性

每次 Agent turn 都会创建一个 `agent_trace`，并用显式 `event_index` 记录：请求、每次模型调用、
每次工具调用、最终回复或错误。Trace 汇总模型和 Prompt 版本、model/tool 调用次数、输入输出
token、首个模型响应、总延迟和估算成本。事件中的常见 API key、邮箱、手机号、地址、收件人和
物流单号字段会脱敏。

按店铺查看最近 Trace 或单次完整时间线：

```bash
curl \
  -H 'X-Tenant-Id: 8741aaf7-d17d-523d-9f6a-f534109d7848' \
  -H 'X-Store-Id: 46267c0e-11d5-5634-9629-07f8f307c42d' \
  http://localhost:8000/api/v1/traces
```

- `GET /api/v1/traces`
- `GET /api/v1/traces/{trace_id}`

Web 控制台提供“审批 / Trace / 评测”三个视图。Trace 视图展示稳定排序的事件时间线；评测视图
展示历史运行、汇总指标和失败用例，并可从失败用例直接跳转到对应 Trace。

### 可选：切换到其他 OpenAI-compatible Provider

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_NAME=<provider-model-name>
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_API_KEY=<provider-api-key>
MODEL_INPUT_COST_PER_MILLION=<input-token-price>
MODEL_OUTPUT_COST_PER_MILLION=<output-token-price>
```

适配层使用 Chat Completions 风格的 `messages`、`tools` 和 `tool_calls` 契约；Runtime、
工具注册表和业务服务不依赖具体模型 SDK。

重置为完全一致的 Demo 数据状态（包括清空审批、退款和优惠券记录）：

```bash
make reset-demo
```
