# 安全边界与已知限制

本项目是本地求职 Demo。它演示“把安全约束放在模型之外”的设计，但没有实现完整的生产身份、网络、
数据保护和运维控制。

## 已实现的控制

| 风险 | 当前控制 | 证据位置 |
|---|---|---|
| 模型覆盖 tenant/store/customer | 身份字段不进入工具参数 Schema；服务端构造 `ToolContext` | `backend/app/tools/context.py`、`registry.py` |
| 跨 scope 读取 | commerce、knowledge、approval 查询显式过滤 tenant/store/customer | `backend/app/commerce/services.py`、`knowledge/service.py`、`approvals/service.py` |
| 模型直接写订单/退款/券 | 只暴露 `request_*`；先创建 `pending_action` | `backend/app/tools/action_tools.py` |
| 重复审批或重复落账 | action 行锁、状态机、执行前复查和 `pending_action_id` 唯一约束 | `backend/app/approvals/service.py`、`models/approvals.py` |
| 检索跨店或历史政策 | 检索前过滤 tenant/store、发布状态、有效期和文档类型 | `backend/app/knowledge/service.py` |
| 检索内容 Prompt Injection | 系统 Prompt 要求资料只作为证据；工具输出经过 presentation boundary | `backend/app/agent/prompts.py`、`presentation.py` |
| 内部工具数据直接返回客户 | 客户会话投影隐藏 tool messages；最终回复规则过滤内部字段和 citation | `backend/app/api/agent.py`、`presentation.py` |
| 失败丢失诊断 | 回滚部分 turn 后单独持久化失败 Trace | `backend/app/observability/service.py` |
| 常见敏感信息进入 Trace | key、地址、邮箱、手机号、收件人、物流单号等字段脱敏 | `backend/app/observability/service.py` |

这些控制在真实 DeepSeek 三次端到端评测中得到有限证据：cross-scope leakage 为 0%，customer
presentation 为 100%。这只覆盖当前 60-case 数据集，不等于形式化安全保证。

## 最重要的可信假设：Header 不是认证

API 直接读取 `X-Tenant-Id`、`X-Store-Id`、`X-Customer-Id` 和 `X-Approver-Id`。它们用于模拟“可信
网关已经认证用户并注入身份”的下游服务环境；当前客户端可以自行填写 header，因此：

- 不能宣传为真实多租户认证或授权；
- 不能把服务原样暴露到公网；
- 审批接口中的 `X-Approver-Id` 也不能证明操作者身份。

当前准确表述是：**在可信身份输入假设下，服务层实现 tenant/store/customer scope 数据隔离**。

## 当前没有的控制

- OAuth/OIDC、session、API token 校验、RBAC/ABAC 或可信反向代理；
- API 速率限制、并发配额、滥用检测和租户级费用预算；
- Provider 429/5xx/瞬时网络错误的 retry/backoff、熔断或 fallback；
- 外部 secret manager 或云端 KMS；本地只用 gitignored `.env.ds` Docker Secret；
- OpenTelemetry、集中告警、生产日志管道、备份/恢复和灾难演练；
- 完整 PII 分类、DLP、数据保留/删除策略和加密密钥治理；
- human handoff 队列或与真实客服/工单系统的集成；
- 公网部署、TLS、WAF、网络隔离或容器供应链签名。

Trace 脱敏和最终回复 sanitizer 都是有限规则集。未知字段、混合格式文本或模型改写可能绕过规则，
因此不应处理真实客户 PII。

## 公开部署的明确风险

`POST /api/v1/evaluations/runs` 当前没有服务端权限与费用保护，会触发 60 次真实模型 case execution；
只靠前端确认框不能阻止直接 API 调用。Approval、Trace 和 Evaluation API 同样依赖可伪造 header。

在补齐以下条件前，建议只在本机受信任环境运行：

1. 在可信网关完成身份验证，并由服务端派生 tenant/store/customer/approver；
2. 为客户、商户、Trace 和评测 API 定义 RBAC 与审计主体；
3. 给真实模型请求增加租户级速率、并发、token 和费用预算；
4. 对 429/5xx/timeout 设计有上限、带 jitter 的 retry，并确保写路径幂等；
5. 使用 secret manager，补齐 TLS、网络隔离、依赖/镜像扫描和备份恢复；
6. 建立 PII inventory、结构化脱敏、保留/删除政策和安全测试；
7. 引入生产可观测性与告警，并将数据库业务 Trace 与平台 telemetry 区分；
8. 真实模型评测达到约定质量门后，再考虑灰度流量。

## 非目标

该 Demo 不追求多 Agent、自主长期规划、并发工具调度、长期用户画像、human handoff 或云原生弹性。
这些能力只有在出现明确问题、可定义评测且不模糊安全边界时才值得加入。
