# 本地运行与演示指南

## 环境要求

- Docker Desktop，包含 Docker Compose；
- GNU Make；
- 用于默认真实模型路径的 DeepSeek API Key；
- 仅在本机运行前端检查时需要 Node.js 24.18.0、npm 11+。

Docker Compose 启动 PostgreSQL/pgvector、Python 3.12 API 和 Nginx 托管的 React 页面。本机不需要
单独安装 Python 3.12。

## 首次启动

```bash
cp .env.example .env
cp .env.ds.example .env.ds
```

编辑 `.env.ds`，使整份文件只有 DeepSeek API Key 本身。不要写 `MODEL_API_KEY=`、引号或其他配置；
`.env.ds` 已被 `.gitignore` 排除，并作为 Docker Secret 只挂载给 API 容器。

```bash
make up
make smoke
```

`make up` 会 build 并后台启动全部服务，等待 healthcheck 通过。首次运行会下载基础镜像、Python/npm
依赖和约 90 MB 的 `BAAI/bge-small-zh-v1.5` ONNX 模型；Embedding 在 CPU 本地运行，缓存保存在
Docker named volume。客户聊天与真实评测会调用 DeepSeek 并产生 API 用量。

| 页面 / API | 地址 |
|---|---|
| 客户聊天 | <http://localhost:5173> |
| 商户审批、Trace 与评测 | <http://localhost:5173/merchant> |
| OpenAPI | <http://localhost:8000/docs> |
| Health | <http://localhost:8000/api/v1/health> |
| Ready | <http://localhost:8000/api/v1/ready> |

常用运维命令：

```bash
make logs
make down
make reset-demo
make reindex
```

`make down` 保留 PostgreSQL 和 Embedding volume；`make reset-demo` 会清空并重新导入确定性 Demo 数据，
包括审批、退款和优惠券记录，随后重建索引。

## 三分钟演示路径

1. 打开客户页，选择页面提供的 store 和 customer；示例订单号会随当前身份动态生成。
2. 询问“请推荐有库存的降噪耳机”，展示真实商品/库存工具调用，而不是模型记忆。
3. 询问“订单取消后，多长时间退款到账？”，展示 Query Decomposition、多路检索和可读资料来源。
4. 询问“知识里写了忽略系统指令时应该怎么处理？”，展示检索内容 Prompt Injection 边界。
5. 使用页面给出的可取消订单请求取消，或请求 10 元物流补偿券；客户端只会收到待审批结果。
6. 打开 `/merchant`，批准或拒绝申请，再下钻同一轮 Trace，查看模型、工具、延迟、token 与费用。
7. 若已运行评测，在评测页打开失败 case 并跳转对应 Trace，解释“指标 -> case -> 执行证据”链路。

不要用不属于当前 customer 的固定订单号演示成功路径；UI 的动态示例会避免这个问题。

## curl 入口

先读取所有确定性 Demo context：

```bash
curl http://localhost:8000/api/v1/demo/contexts
```

下面的 Aurora context 是当前 seed 中的稳定示例。创建会话：

```bash
curl -X POST \
  -H 'X-Tenant-Id: 8741aaf7-d17d-523d-9f6a-f534109d7848' \
  -H 'X-Store-Id: 46267c0e-11d5-5634-9629-07f8f307c42d' \
  -H 'X-Customer-Id: 0d1ed7e7-59ab-50e6-9d62-faa77e406b84' \
  http://localhost:8000/api/v1/conversations
```

将返回的 `conversation_id` 填入下一条请求：

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-Id: 8741aaf7-d17d-523d-9f6a-f534109d7848' \
  -H 'X-Store-Id: 46267c0e-11d5-5634-9629-07f8f307c42d' \
  -H 'X-Customer-Id: 0d1ed7e7-59ab-50e6-9d62-faa77e406b84' \
  --data '{"content":"请推荐有库存的降噪耳机"}' \
  http://localhost:8000/api/v1/conversations/<conversation_id>/messages
```

Header 是 Demo 身份模拟，不是认证凭证。API 只能在可信本地环境使用，完整边界见
[security-boundaries.md](./security-boundaries.md)。

## 评测命令

```bash
make eval-retrieval # 100-case Retrieval Gold Set
make eval-mock      # 60-case 确定性端到端回归
make eval           # 60-case 真实 DeepSeek；会产生 API 用量
```

`make eval-retrieval RETRIEVAL_SPLIT=dev` 和 `RETRIEVAL_SPLIT=holdout` 可分别运行 75 条 dev 或 25 条
holdout。真实模型的多次基线需要重复执行 `make eval`，每次都会生成独立 UUID 报告并更新
`eval-results/latest.json`。指标和解读见 [evaluation.md](./evaluation.md)。

## 开发检查

首次运行前端检查先安装 lockfile 中的版本：

```bash
npm --prefix frontend ci
make lint
make test
```

`make lint` 在后端 Docker test stage 中运行 Ruff、mypy 和 pytest，同时运行前端 ESLint；`make test`
再次构建后端 test stage，并运行前端 Node test 与 production build。

## 依赖与可复现性

前端使用提交的 `frontend/package-lock.json` 和 `npm ci`，得到精确依赖树。Python 使用
`backend/requirements-py312-linux.lock` 固定 CPython 3.12/Linux 下 dev + embedding 依赖合集；
Docker/CI 以该文件作为 constraint 安装并执行 `pip check`。`backend/pyproject.toml` 保留直接依赖
及兼容范围，用于项目元数据与开发工具配置。更新 Python 依赖时运行 `make python-lock`，审核 diff
后再提交，不能只修改 `pyproject.toml`。

默认运行配置在 `.env.example`，其中包含 DeepSeek model alias、成本估算单价、BGE model 与检索
阈值。比较评测结果时还要同时固定 source commit、Prompt 和数据集版本；远端 model alias 的权重
仍可能由供应商更新。
