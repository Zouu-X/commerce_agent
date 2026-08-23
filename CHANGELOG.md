# Changelog

本文件记录面向 GitHub Release 的项目变化。格式参考
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，版本遵循
[Semantic Versioning](https://semver.org/)。

## Unreleased

当前没有未发布变更。

## 0.1.0 - 2026-08-23

### Added

- 确定性多 tenant 电商沙盒、FastAPI API、PostgreSQL/pgvector 和 React 双角色界面。
- Provider-independent tool-calling Runtime，含对话持久化、预算、超时、结构化工具注册与数据库
  Trace。
- 7 个只读工具，以及取消订单、退款和发券的 3 个待审批请求工具。
- 事务化人工审批状态机，含执行前复查、行锁、审计记录和结果唯一约束。
- PostgreSQL 全文检索、BGE 向量检索、加权 RRF 和确定性 Query Decomposition。
- 60-case 端到端评测与 100-case、owner-reviewed Retrieval Gold Set。
- 冻结的 Retrieval `96/100` 基线，以及真实 DeepSeek 三次 `81/180` 问题基线。
- Docker Compose 一键本地运行、包含前后端检查的 CI 与锁定依赖。
- 面向公开展示的精简 README、技术文档、MIT License 与发布说明。

### Known limitations

- Demo header 只模拟可信身份，没有实现认证、授权或可信网关。
- 真实 DeepSeek 三次 pooled pass rate 为 45.00%，质量门未通过。
- 没有 Provider retry/backoff、速率/费用保护、OpenTelemetry、human handoff 或公开部署配置。
