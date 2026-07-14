# Commerce Support Agent

一个可评测、可观测的多租户电商客服 Agent 沙盒。项目当前已具备确定性电商业务沙盒、
Provider-independent Agent Runtime 和 6 个安全只读工具，详细范围见
[`project_plan.md`](./project_plan.md)。

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
tenant、2 家店铺、12 位顾客、24 个商品、48 个 SKU、60 个订单，以及物流和售后边界场景。

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

重置为完全一致的 Demo 数据状态：

```bash
make reset-demo
```
