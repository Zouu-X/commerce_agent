# Commerce Support Agent v0.1.0

## 这个版本是什么

`v0.1.0` 是第一个完整求职 Demo 版本：它把确定性电商沙盒、Provider-independent Agent
Runtime、Hybrid RAG、人工审批写操作、数据库 Trace、离线评测和双角色 Web 界面组成一条可运行、
可下钻的证据链。

## 核心能力

- 7 个只读工具与 3 个只创建 `pending_action` 的请求工具；
- tenant/store/customer service scope，身份字段由服务端注入工具 context；
- PostgreSQL FTS + BGE/pgvector + weighted RRF + deterministic Query Decomposition；
- 取消、退款和发券的审批状态机，含行锁、执行前复查、审计和幂等约束；
- request/model/tool/response/error Trace，记录 token、延迟、费用和失败证据；
- React 客户聊天页和商户审批/Trace/评测页；
- Docker Compose 本地启动和 CI 检查。

## 可核验结果

- owner-reviewed Retrieval Gold Set：`96/100`，全量/dev/holdout 聚合质量门均通过；
- 真实 DeepSeek：三次共 `81/180`，pooled pass rate `45.00%`，三次质量门均未通过；
- 三次真实模型总估算费用 `$0.12606426`，P95 范围 4,527–5,017 ms；
- 冻结报告见 [Retrieval baseline](./evals/retrieval-baseline-7e8a925.md) 和
  [DeepSeek 3-run baseline](./evals/deepseek-3run-baseline-7e8a925-20260823.md)。

## 已知限制

- Scope header 只模拟可信身份输入，不是真实认证/授权；
- 真实模型工具选择、任务完成、action 和 citation 指标尚未达门；
- 没有 Provider retry/backoff、速率/费用保护、OpenTelemetry、完整 DLP、human handoff 或公网部署；
- 工具调用顺序执行，不包含多 Agent 或长期记忆。

## 发布验收

- 后端 Ruff、mypy、109 个 pytest 通过；
- 前端 3 个 Node tests、ESLint 和 production build 通过；
- Python 3.12/Linux 锁文件由 CI、Docker test 与 runtime 安装共同消费；
- README 与技术文档相对链接、Markdown 格式和冻结评测引用检查通过。
