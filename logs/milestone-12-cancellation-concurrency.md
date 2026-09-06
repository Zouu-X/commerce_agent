# Milestone 12：用订单行锁修复并发取消申请去重

## 问题与原因

Milestone 11 的 Mock eval 中，`cancel_concurrent_sessions` 失败：两个会话针对同一订单
创建了两份取消申请。旧代码先查询有效申请，没有则插入，但两个事务可以同时读到“没有申请”。
现有唯一键包含 conversation_id、trace_id 和完整 payload，不同会话的 key 不同，数据库不会
把它们视为重复。审批时锁住同一份申请只能防止重复执行，无法阻止两份申请的创建。

## 本次修复

分支：`codex/fix-cancellation-concurrency`，等待用户 review，未合并。

`OrderService.get_order` 增加可选的 `for_update` 参数。取消申请在查询订单时使用
`SELECT … FOR UPDATE`，随后查询有效申请，有则返回原申请，没有才检查状态并创建。
订单查询继续限制 tenant/store/customer，避免绕过原有权限边界。

两次请求的顺序变为：A 锁住订单 → A 创建申请并提交 → B 获得订单锁 → B 查到并复用 A 的申请。
必须锁定已经存在的订单；锁定尚不存在的申请行不能解决这个问题。锁一直持有到调用方事务
提交或回滚，不在 service 内提前 commit，以保留消息、Trace、申请的事务边界。

锁定查询使用 `populate_existing=True`，刷新当前 session 可能已经加载的订单，避免等待其他
事务完成后继续用旧状态判断。普通只读查询不加锁。没有改通用幂等键、Prompt、Mock 路由或评分契约，
没有增加数据库迁移、Redis 锁或部分唯一索引。

## 验证与证据

- 后端 120/120 测试通过，包含真实 PostgreSQL 并发测试；ruff、mypy 通过。
- 原先证明重复申请的测试改为在订单查询前同步两个请求，检查两个真实后端 PID、同一个
  action_id、一份申请，以及 `created` / `already_pending` 两种返回和完整审批后的 verifier 结果。
  同步点不能放在获得订单锁之后，否则第二个事务等锁，第一个事务等 barrier，会死锁。
- 保留不同会话、不同取消原因复用申请的测试；新增拒绝后新请求可以创建新申请的回归。
- 正式 CLI 定向运行 `cancel_concurrent_sessions`，Mock + 模板用户 + PostgreSQL，Embedding 为 hash。
  结果 **1/1 通过、22/22 必需检查通过、质量门通过**；不是全量 21-case 重跑。
- 两个 Agent 连接 PID 为 9940、9941，返回同一个申请
  `0cd31609-5571-46c4-b2b7-1e7461556fb4`，分别为 `already_pending` 和 `created`。
  最终数据库仅一份申请，订单正确取消，审批审计及未审批写入检查通过；没有异常。

Run ID：`79770f1d-c8e8-4a60-9bd2-7c5e4a47faaa`。

原始报告：`eval-results/cancellation-lock-fix/evaluation-79770f1d-c8e8-4a60-9bd2-7c5e4a47faaa.json`。
报告 SHA-256：`d0515248fa3064129bbe091294a4c7122290a88a9c962caa3c059ec983f2319a`。
Application SHA-256：`441dd831939671db0b4a32e0d915f9763f54fac6b35d9900a320e0d6854c350c`。
定向 Dataset SHA-256：`04b2e5c40a2635b4b85d5535c736554ed5103fea118636aafed2b07035f889f0`。

复现命令（从 backend 目录运行，使用项目默认本地 PostgreSQL 配置）：

```bash
MODEL_PROVIDER=mock MODEL_NAME=mock-commerce-agent \
MODEL_INPUT_COST_PER_MILLION=0 MODEL_OUTPUT_COST_PER_MILLION=0 \
EMBEDDING_PROVIDER=hash ../.venv/bin/python -m app.evaluations.cli \
  --case cancel_concurrent_sessions --simulator template \
  --output-dir ../eval-results/cancellation-lock-fix

EVAL_TEST_DATABASE_URL=postgresql+asyncpg://commerce:commerce@localhost:5432/commerce \
  ../.venv/bin/pytest
```

## 取舍与面试讲法

这是适合 Demo 的局部修复：依赖所有取消申请入口遵循同一服务流程；绕过服务直接插入仍没有
业务唯一约束兜底。SQLite 测试不能证明行锁行为，真实并发结论来自 PostgreSQL。
当前事务覆盖整个 Agent turn，因此生成工具后的最终回复期间也可能持锁；没有改变事务边界，
后续若扩展为长流程，需要另行设计短事务，而不是在本次修复中随意提前提交。

面试时可以说：“评测暴露了先查后写的竞态。原来的幂等键区分请求，但没有约束同一业务目标。
我在创建取消申请前锁住订单，让同一订单的检查和创建串行执行，并通过两个真实数据库连接
验证只产生一个申请、审批后状态正确。这次修复针对业务层，模型和评测答案都没有调整。”
