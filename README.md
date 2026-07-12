# Commerce Support Agent

一个可评测、可观测的多租户电商客服 Agent 沙盒。项目当前处于工程基线阶段，详细范围见
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