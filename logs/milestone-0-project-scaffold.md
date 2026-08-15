# Milestone 0：项目脚手架与可运行工程基线

完成日期：2026-07-12

## 1. 这个里程碑解决了什么问题

Milestone 0 的目标不是实现 Agent 能力，而是先建立一套可重复启动、可自动检查、方便继续扩展的工程基线。

项目后续会涉及数据库、工具调用、审批状态机、可观测性和评测。如果一开始没有固定运行环境、目录边界和质量检查，后面的业务问题很容易和环境问题混在一起。因此这一阶段先把前端、后端、数据库、容器化和 CI 串成最小闭环。

最终达到的效果是：开发者只需要安装 Node.js 和 Docker Desktop，就可以通过 Docker Compose 启动 Web、API 和 PostgreSQL，并用统一命令完成前后端检查。

## 2. 建立的项目结构

```text
commerce_agent/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   └── main.py          # 应用入口和健康检查
│   ├── tests/               # 后端测试
│   ├── Dockerfile           # 后端运行与测试镜像
│   └── pyproject.toml       # Python 依赖和工具配置
├── frontend/                # React 前端
│   ├── src/
│   ├── Dockerfile           # Vite 构建 + Nginx 运行镜像
│   ├── package.json
│   └── package-lock.json
├── .github/workflows/ci.yml # GitHub Actions
├── docker-compose.yml       # db、api、web 编排
├── Makefile                 # 统一开发命令
├── .env.example             # 环境变量示例
└── .node-version            # Node 版本约束
```

这里先建立清晰的前后端边界，但没有提前创建大量空的业务目录。随着 Milestone 1 开始实现数据模型和服务层，再按实际职责增加模块。

## 3. 后端做了什么

### 3.1 FastAPI 最小应用

后端使用 Python 3.12 和 FastAPI，提供了：

```http
GET /api/v1/health
```

正常响应为：

```json
{"status": "ok"}
```

健康检查的作用不只是展示一个接口。Docker、部署平台和后续监控系统都需要一个低成本端点判断 API 进程是否能够正常处理请求。

应用使用 `create_app()` 工厂函数创建。当前功能很少，但这种方式便于后续注册路由、生命周期事件、中间件和测试配置，而不需要重写入口结构。

### 3.2 Python 工程配置

`backend/pyproject.toml` 集中管理：

- 运行依赖：FastAPI、Uvicorn；
- 测试依赖：pytest、HTTPX、AnyIO；
- 静态检查：Ruff；
- 类型检查：mypy strict mode；
- Python 版本：`>=3.12,<3.14`。

选择严格类型检查，是因为这个项目之后会处理工具参数、可信身份上下文和业务状态。尽早约束类型，可以减少订单金额、ID、可空状态等字段在业务层中被错误传递。

### 3.3 后端测试

健康检查测试通过 HTTPX 的 `ASGITransport` 直接调用 FastAPI 应用，不需要真正占用本机端口。这样测试速度快，也能验证真实的 ASGI 请求链路。

测试阶段会执行：

```bash
ruff check .
mypy
pytest
```

### 3.4 后端 Docker 多阶段构建

后端 Dockerfile 包含两个用途不同的目标：

- `test`：安装开发依赖，并执行 Ruff、mypy 和 pytest；
- `runtime`：只安装生产运行所需依赖并启动 Uvicorn。

这样生产 API 镜像不需要携带测试工具，同时 CI 和没有本地 Python 3.12 的开发者仍然可以在一致的 Linux 环境中运行检查。

## 4. 前端做了什么

前端使用：

- Node.js 24.18.0；
- React 19；
- TypeScript 6；
- Vite 8；
- ESLint 10。

当前页面只是工程就绪页，还没有开始实现顾客聊天和 Agent 控制台。这个阶段重点验证 TypeScript、样式、生产构建和容器运行链路。

Node 版本写入 `.node-version`，依赖版本写入 `package-lock.json`。两者分别解决运行时版本漂移和依赖解析结果漂移的问题。

前端 Dockerfile 使用多阶段构建：

1. 在 Node Alpine 镜像中执行 `npm ci` 和 `npm run build`；
2. 只把生成的静态文件复制到 Nginx Alpine 镜像；
3. 最终运行镜像不包含 Node、源码和开发依赖。

这比直接用 Vite 开发服务器作为生产服务更小、更稳定，也更接近真实部署方式。

## 5. Docker Compose 如何组织服务

Compose 当前包含三个服务：

| 服务 | 镜像 | 作用 | 本机端口 |
|---|---|---|---|
| `db` | `postgres:17-alpine` | PostgreSQL 数据库 | `5432` |
| `api` | 项目构建的 FastAPI 镜像 | 后端 API | `8000` |
| `web` | 项目构建的 Nginx 镜像 | 前端静态页面 | `5173` |

数据库配置了 `pg_isready` 健康检查。API 不只是声明依赖数据库容器，而是等待数据库进入 healthy 状态后才启动。这避免了“容器已经创建，但数据库还不能接受连接”导致的启动竞争问题。

PostgreSQL 数据使用命名卷 `postgres_data` 保存。执行 `docker compose down` 会删除容器和网络，但不会删除数据卷，便于继续开发；如果以后需要重置 Demo 数据，会提供单独且明确的 reset 命令。

## 6. 统一开发命令

Makefile 提供了统一入口：

```bash
make up      # 构建并启动全部服务
make down    # 停止并删除容器和网络
make logs    # 查看 Compose 日志
make lint    # 后端 Ruff/mypy + 前端 ESLint
make test    # 后端测试 + 前端生产构建
```

使用 Makefile 的原因是避免 README、CI 和开发者各自维护一套不同的长命令。后续加入迁移、seed、reset 和 eval 时，也会继续通过 Makefile 暴露稳定入口。

当前 `make lint` 和 `make test` 都会构建 Dockerfile 的 `test` 目标。如果不指定镜像标签，Docker Desktop 可能显示无名称的 `<none>:<none>` 测试镜像。这些是已完成检查的临时构建结果，不是业务服务镜像，可以通过 `docker image prune` 清理。后续可通过给测试镜像固定标签或合并检查目标来优化。

## 7. CI 做了什么

GitHub Actions 配置了两个独立 job：

### Backend

1. 安装 Python 3.12；
2. 安装项目和开发依赖；
3. 执行 Ruff；
4. 执行 mypy；
5. 执行 pytest。

### Frontend

1. 按 `.node-version` 安装 Node；
2. 使用 `npm ci` 严格按 lockfile 安装；
3. 执行 ESLint；
4. 执行 TypeScript 和 Vite 生产构建。

前后端 job 分开后，失败位置更容易定位，也能并行执行。当前配置已经在本地以相同命令验证，推送到 GitHub 后由 Actions 做远程验证。

## 8. 配置和仓库卫生

这一阶段还补充了：

- `.env.example`：只保存可公开的开发默认值，不提交真实密钥；
- `.gitignore`：忽略 `.env`、Python 缓存、虚拟环境、`node_modules` 和构建产物；
- 前后端 `.dockerignore`：避免把缓存、虚拟环境和 `node_modules` 发送到 Docker build context；
- README：记录环境要求、启动地址和常用命令。

这些文件看起来不是业务功能，但它们决定了项目能否被招聘方快速、稳定地复现。

## 9. 实际验收结果

本里程碑不是只完成配置文件，而是做了完整运行验证：

1. `docker compose up -d --build` 成功构建并启动三个服务；
2. PostgreSQL 状态为 healthy；
3. `pg_isready` 返回 accepting connections；
4. 数据库执行 `SELECT 1` 成功；
5. `http://localhost:8000/api/v1/health` 返回 `{"status":"ok"}`；
6. `http://localhost:5173` 返回构建后的前端页面；
7. `make lint test` 完整通过；
8. npm audit 结果为 0 个已知漏洞。

验证结束后使用 `docker compose down` 停止并删除了容器和网络，保留 PostgreSQL 数据卷和构建镜像。

## 10. 遇到的问题和处理方式

### Node 和 Docker 安装后当前终端暂时找不到命令

Node 通过 fnm 安装，Docker Desktop 也刚完成安装。Codex 进程最初继承的是安装前的 PATH，因此短时间内无法直接找到命令。通过确认实际安装路径完成了初始验证；Docker Desktop 完成首次初始化后，标准 `node`、`npm` 和 `docker compose` 命令恢复正常。

这个问题属于开发环境初始化，而不是项目代码问题。项目通过 `.node-version` 和 Dockerfile 固定运行版本，避免把某台机器的临时 PATH 写死到仓库。

### Docker Desktop 首次启动时引擎尚未就绪

Docker CLI 已安装，但第一次构建时 daemon socket 尚不存在。完成 Docker Desktop 的许可和初始化后，Docker Engine 29.6.1 正常运行，随后完整构建通过。

### 测试客户端出现上游弃用警告

初版健康检查使用 FastAPI/Starlette 的 `TestClient`，当前依赖组合提示 HTTPX 接口即将变化。测试随后改为 HTTPX `AsyncClient` 配合 `ASGITransport`，既消除了警告，也更直接地测试异步 ASGI 应用。

## 11. 面试时如何讲这一阶段

可以按下面的顺序说明：

> 我没有一开始就接入 LLM，而是先完成可重复运行的工程基线。后端使用 Python 3.12 和 FastAPI，前端使用 React、TypeScript 和 Vite，PostgreSQL 运行在 Docker 中。Compose 通过数据库健康检查控制 API 启动顺序，前后端都使用多阶段镜像区分构建环境和生产运行环境。我用 Ruff、mypy、pytest、ESLint 和 TypeScript 建立质量门禁，并在 GitHub Actions 中拆成两个独立 job。最后不是只检查配置，而是真正启动三个容器，验证了 HTTP 接口、前端页面和数据库查询，再运行统一的 `make lint test` 完成验收。

这一阶段最重要的设计取舍是：先保证环境可复现和质量门禁可靠，再实现电商领域模型和 Agent。这样后续遇到问题时，可以更清楚地区分环境、业务规则和模型行为。

## 12. 当前边界与下一步

Milestone 0 只证明工程能够可靠运行，还没有实现：

- SQLAlchemy 数据模型和 Alembic 迁移；
- tenant、store、customer 数据隔离；
- 商品、订单和物流服务；
- 确定性 seed 数据；
- Agent、工具调用和审批工作流。

Milestone 1 将先构建一个完全不依赖 LLM 的电商业务沙盒。重点是领域模型、数据库迁移、确定性模拟数据、服务层以及跨租户和跨顾客访问失败测试。只有这些确定性安全边界可靠以后，才会在下一阶段把它们暴露为 Agent 工具。
