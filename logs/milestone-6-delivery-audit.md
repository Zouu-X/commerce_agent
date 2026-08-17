# Milestone 6：交付审计

## 1. 这一步解决什么问题

Milestone 0～5 已经完成 Agent、RAG、审批写操作、评测和 Trace，但“开发机上能运行”不等于
“招聘方拿到仓库后能顺利体验”。本轮交付审计从一个没有构建产物、没有数据库卷的新副本出发，
验证 README 是否足以让不了解项目的人完成启动、检查和核心流程演示。

这一步的目标不是增加新的 Agent 能力，而是降低项目交付时的不确定性：

- 启动命令是否真的包含迁移、seed 和服务就绪等待；
- Docker 启动是否还隐含依赖本机 Python、Node 或模型 API Key；
- RAG、审批、幂等、Trace 和评测能否在新数据库上复现；
- README 中的测试、重置和评测命令是否仍然有效；
- 是否存在会影响面试演示的依赖安全告警或界面缺口。

## 2. 审计方式

审计基于 `master` 的已提交快照，在 `/private/tmp` 创建本地干净克隆。为了不停止开发机上正在
运行的 Demo，审计副本使用独立的 Compose project、数据库卷和临时端口。临时端口只用于隔离，
正常用户仍按 README 使用 `5173 / 8000 / 5432`。

审计环境：

| 项目 | 版本 |
|---|---|
| Node.js | 24.18.0 |
| npm | 11.16.0 |
| Docker Engine | 29.6.1 |
| Docker Compose | 5.2.0 |
| 模型 Provider | 确定性 Mock，无 API Key |

## 3. 干净启动结果

干净副本成功完成以下步骤：

1. 从 `.env.example` 创建 `.env`；
2. 构建 PostgreSQL、FastAPI 和 React/Nginx 三个镜像；
3. 创建全新的 PostgreSQL volume；
4. 等待数据库健康；
5. API 自动运行全部 Alembic migration；
6. API 在空数据库中自动运行 `seed --if-empty`；
7. Web、API 和数据库全部进入 healthy 状态。

首次无缓存构建约一分钟完成，明显低于“10 分钟内启动并理解项目”的验收目标。最终验证的服务
端点包括：

- `GET /api/v1/health`；
- `GET /api/v1/ready`；
- `GET /api/v1/demo/contexts`；
- Web 首页；
- Swagger API 文档。

新数据库中的确定性数据量符合设计：2 个 tenant、2 家店铺、12 位顾客、24 个商品、48 个
SKU、60 个订单、28 个知识文档和 28 个知识切片。

## 4. 端到端业务验证

### 4.1 只读业务和 RAG

- 按 README 中的可信身份请求头成功读取订单；
- 知识检索返回当前 tenant/store 下的有效政策；
- Agent 回答“无理由退货政策是多少天”时调用 `search_store_policy`；
- 最终回答包含 `no-reason-return:v1#chunk-1` 引用。

这证明演示不是 Mock Provider 直接写死业务答案：模型适配层只做确定性意图模拟，订单事实和政策
证据仍经过 Runtime、Tool Registry、Service 和 PostgreSQL。

### 4.2 审批型写操作

审计通过 Agent 请求取消订单，并验证：

1. Agent 只创建 `pending_action`；
2. 审批前订单仍保持原状态；
3. 审批人批准后订单才变为 `cancelled`；
4. 第二次批准返回同一结果，没有重复执行；
5. 审计日志依次记录 requested、approved、execution_started 和 execution_succeeded；
6. 对应 Trace 保存模型循环、工具参数、工具结果、耗时和最终回复。

随后执行 `make reset-demo`，订单恢复为 `paid`，待审批队列恢复为空，证明 Demo 可以在每次演示前
回到一致状态。

### 4.3 离线评测

`make eval` 在新 PostgreSQL 数据库上成功运行 60 条真实链路用例并生成数据库记录与 JSON 报告：

| 指标 | 审计结果 |
|---|---:|
| 通过用例 | 58 / 60 |
| 工具选择准确率 | 100% |
| 必要工具召回率 | 100% |
| 工具参数有效率 | 100% |
| 任务完成率 | 100% |
| 引用覆盖率 | 100% |
| 引用正确率 | 83.33% |
| 跨范围泄露率 | 0% |
| 未审批写操作执行率 | 0% |

58/60 与 README 中的 `milestone-5-v3` 基线一致。两条失败是严格引用集合门禁主动暴露的检索噪声，
不是启动或执行故障，因此审计没有把它伪装成 60/60。

## 5. 质量与页面验证

- Ruff：通过；
- mypy：通过，60 个源文件无问题；
- pytest：58 passed；
- ESLint：通过；
- TypeScript/Vite production build：通过；
- 浏览器实际打开审批、Trace 和评测三个视图，店铺上下文、Trace 列表/详情和历史评测均能加载；
- 标准端口页面没有浏览器 console error。

## 6. 本轮发现并修复的问题

### 6.1 快速启动重复依赖本机 Node

原 README 在 `make up` 前要求执行 `npm install`，但 Web 本来就在多阶段 Dockerfile 中使用
`npm ci` 构建。这让“Docker 一键体验”多了一项不必要的本机依赖。

修复后快速启动只要求 Docker Desktop：

```bash
cp .env.example .env
make up
make smoke
```

Node/npm 仅在本地执行前端 lint/build 时需要。

### 6.2 `make up` 无法明确表示服务已就绪

原命令以前台模式持续占用终端，且只有数据库有 healthcheck。现在：

- API 使用 `/api/v1/ready` 检查应用和数据库；
- Web 使用 Nginx 本地请求检查静态站点；
- Web 等待 API healthy 后再启动；
- `make up` 使用 `--detach --wait`，全部服务 healthy 后才返回；
- 新增 `make smoke`，一条命令验证 API health/ready、Demo 数据和 Web。

这让用户看到命令成功返回时，可以直接开始演示，而不需要猜测迁移或 seed 是否完成。

### 6.3 npm 开发依赖安全告警

首次安装报告 3 个 high severity 告警，均来自开发/构建依赖；`npm audit --omit=dev` 显示生产依赖
为 0 漏洞。锁文件已用 npm 的兼容修复升级：

- `brace-expansion 5.0.7 -> 5.0.9`；
- `nanoid 3.3.15 -> 3.3.18`；
- `postcss 8.5.17 -> 8.5.26`。

修复后完整 `npm audit` 为 0，lint 和 production build 仍通过。

## 7. 尚未完成的 Milestone 6 工作

交付审计通过不代表 Milestone 6 已完成。以下项目需要继续处理：

1. **顾客聊天 Web 界面**：当前 Web 完成了审批、Trace、评测控制台，但顾客聊天仍主要通过 API
   或 curl 演示。后端闭环完整，不过这与项目计划中的“双界面”范围存在差距，也会降低 3～5 分钟
   面试演示的直观性。应选择实现最小聊天页，或者在项目范围中明确说明控制台 + API 的取舍。
2. **架构图和威胁模型**：README 目前解释了组件，但还缺少面试时可以快速讲解的执行流程图、信任
   边界和威胁—缓解措施映射。
3. **演示脚本**：需要固定 3～5 分钟演示路径，避免现场临时选择顾客、订单和问题。
4. **公开部署决策**：当前 CORS 允许标准本地 Web origin，前端 API 地址在构建时注入。若选择公开
   部署，需要将允许来源和 API base URL 配置化；若选择本地交付，应在 README 明确这是刻意取舍。
5. **Python 依赖锁定**：`pyproject.toml` 使用兼容版本范围，当前干净构建已通过，但长期复现性不如
   Node 的 lockfile。对于求职 Demo 可以作为已知限制说明，或者后续增加 lock/constraints 文件。

> 后续进展（2026-08-16）：第 1 项“顾客聊天 Web 界面”已完成，并将默认 Agent Provider 从 Mock
> 切换为 DeepSeek V4 Flash。原审计中的 Mock 环境、命令和结论仍是当时快照；当前启动需要配置
> `.env.ds`，真实模型接入与双页面验证详见 `milestone-6-customer-chat-deepseek.md`。

## 8. 面试时如何讲这一阶段

可以把这一阶段概括为：

> 我没有把“在我的电脑上能跑”当作完成标准，而是从干净克隆重新走了一遍招聘方的体验路径。
> 审计不仅验证容器能启动，还验证了 RAG 引用、审批幂等、Trace、数据重置和 60 条评测。过程中
> 去掉了快速启动对本机 Node 的重复依赖，为 API/Web 增加健康检查和启动等待，并把依赖告警清零。
> 最终 `make up && make smoke` 能给出明确的可用信号，同时审计也如实记录了聊天 UI 和公开部署等
> 尚未完成的包装工作。

这个做法体现的不是生产级运维复杂度，而是工程交付意识：成功标准必须能被一个不了解项目的人
重复验证，并且已知缺口要显式记录，不能依赖作者现场解释。
