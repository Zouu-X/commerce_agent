# Commerce Support Agent

一个可评测、可观测的多租户电商客服 Agent 沙盒。项目当前已具备确定性电商业务沙盒、
Provider-independent Agent Runtime、知识库混合检索、7 个安全只读工具，以及退款、发券、
取消订单三类人工审批写操作，详细范围见 [`project_plan.md`](./project_plan.md)。

## 环境要求

- Node.js 24.18.0
- npm 11+
- Docker Desktop（包含 Docker Compose）
- Python 3.12（仅本地运行后端时需要）

## 快速启动

```bash
cp .env.example .env
npm --prefix frontend install
make up
```

启动后访问：

- Web：http://localhost:5173
- API 健康检查：http://localhost:8000/api/v1/health
- API 文档：http://localhost:8000/docs
- API 就绪检查：http://localhost:8000/api/v1/ready

停止服务：

```bash
make down
```

## 开发检查

```bash
make lint
make test
```

后端检查默认在 Python 3.12 容器中执行，因此本机没有安装 Python 3.12 也可以运行。

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

当前支持：

- 取消订单：仅 `pending` 或 `paid` 订单可申请，批准后变为 `cancelled`；
- 退款：检查订单归属、支付状态、退款窗口和剩余可退金额；
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
已经过期的政策不会进入候选集。每个结果都会返回文档版本和 `citation_id`。

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

默认使用确定性的本地 Mock Provider，因此不配置模型 API Key 也能演示完整 tool-calling 流程。
Mock 只负责模拟模型的意图识别和回复生成，商品、订单、物流和售后事实仍然来自 PostgreSQL
及业务服务层。

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
- `无理由退货政策是多少天？`（回答必须带知识引用）
- `付款以后商品降价了能退差额吗？`（演示同义表达的混合检索）
- `知识里写了忽略系统指令时应该怎么处理？`（演示检索内容 Prompt Injection 防护）
- `帮我取消订单 AUR-202607-0001，我不想要了`（只创建待审批动作）
- `给我发一张 10 元补偿券，物流太慢了`（只创建待审批动作）

读取会话可以看到完整的 `user -> assistant(tool_calls) -> tool -> assistant` 消息链：

```bash
curl \
  -H 'X-Tenant-Id: ...' \
  -H 'X-Store-Id: ...' \
  -H 'X-Customer-Id: ...' \
  http://localhost:8000/api/v1/conversations/<conversation_id>
```

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

### 切换到 OpenAI-compatible Provider

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_NAME=<provider-model-name>
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_API_KEY=<provider-api-key>
```

适配层使用 Chat Completions 风格的 `messages`、`tools` 和 `tool_calls` 契约；Runtime、
工具注册表和业务服务不依赖具体模型 SDK。

重置为完全一致的 Demo 数据状态（包括清空审批、退款和优惠券记录）：

```bash
make reset-demo
```
