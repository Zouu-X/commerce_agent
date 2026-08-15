# Milestone 4：人工审批、安全写操作与幂等执行

完成日期：2026-08-15

## 1. 里程碑目标

Milestone 3 让 Agent 能检索店铺政策并给出引用，但整个系统仍只有读取能力。退款、发券和取消
订单会真实改变业务状态，如果让模型直接执行，会产生四类风险：

1. 模型可能误判用户意图或生成错误参数；
2. 用户可能通过 Prompt Injection 诱导模型越权写入；
3. 从申请到执行之间订单状态可能已经变化；
4. 网络重试或重复点击可能造成重复退款、重复发券。

本阶段的核心不是简单增加三个写工具，而是把“模型建议”和“业务执行”结构性分离：

```text
用户请求退款 / 发券 / 取消订单
  -> Agent 调用 request_* 工具
  -> 服务端注入 tenant/store/customer/conversation/trace
  -> 确定性规则做首次校验
  -> 只创建 pending_action，不修改业务数据
  -> 人工审批页面显示动作、原因和审计记录
  -> 审批人批准
  -> 数据库锁定 action 和目标订单
  -> 再次校验归属、状态、金额和时间窗口
  -> 幂等执行一次
  -> 保存结果和完整审计事件
```

验收目标是：未批准的动作不能改变订单、支付或优惠券数据；重复批准同一个动作不会重复退款或
重复发券；其他 tenant/store 的审批人看不到也无法操作该动作。

## 2. 为什么采用“申请工具 + 审批服务”两段式设计

Agent Runtime 中注册了三个新工具：

```text
request_order_cancellation
request_refund
request_coupon
```

这些名称刻意使用 `request_`，因为调用成功只代表“申请已记录”，不代表业务动作已经完成。工具
结果明确返回：

```json
{
  "action_id": "...",
  "action_type": "cancel_order",
  "status": "pending",
  "requires_approval": true,
  "payload": {}
}
```

系统 Prompt 要求 Agent 把 `action_id` 告诉用户，并说明审批前不会修改业务数据。即使模型把
工具调用错了，最坏结果也只是出现一条可拒绝的待审批记录，而不是立即产生资金或订单副作用。

真正写入由独立的 `ApprovalService` 完成。这样权限边界不依赖模型是否“听话”，而是由代码、
数据库事务和数据模型共同保证。

## 3. 数据模型与状态机

新增 Alembic revision：

```text
20260815_0004
```

迁移新增四张表：

```text
pending_actions
  ├── action_audit_logs
  ├── refund_transactions
  └── coupon_grants
```

### 3.1 `pending_actions`

每条动作保存：

- `tenant_id`、`store_id`、`customer_id`：业务作用域；
- `conversation_id`、`trace_id`：回溯提出申请的对话和请求；
- `action_type`：`refund`、`issue_coupon` 或 `cancel_order`；
- `payload_json`：订单号、金额、原因等经过校验的执行参数；
- `idempotency_key`：请求级幂等键；
- `requested_by`、`reviewed_by`：申请者与审批者；
- `result_json`、`failure_code`：结构化执行结果或失败原因；
- 创建、审批和执行时间。

当前状态机为：

```text
                  ┌────────────── reject ─────────────> rejected
                  │
pending ── approve ──> approved ──> executing ──> succeeded
                                           └────> failed
```

`approved` 和 `executing` 在同一个数据库事务中推进，审计日志仍会分别记录这两个阶段。当前 Demo
采用同步执行，后续接入消息队列时可以保持状态机不变，把 `executing` 交给异步 worker。

### 3.2 `action_audit_logs`

审批动作不是只保存一个最终状态，而是追加事件：

```text
requested
approved / rejected
execution_started
execution_succeeded / execution_failed
```

每个事件带单调递增的 `event_index`、`actor_type`、`actor_id`、时间和结构化详情。不能只按
`created_at` 排序：PostgreSQL 的 `now()` 在同一事务中保持相同值，批准、开始执行和执行成功可能
拥有完全相同的时间。关系按 `(action_id, event_index)` 唯一约束并排序，重新加载后仍能稳定还原
真实事件顺序。审批页面可以展开审计时间线，面试演示时能清楚说明“谁在什么时候提出、批准并
执行了什么”。

### 3.3 `refund_transactions` 与 `coupon_grants`

退款和优惠券没有只写进 `result_json`，而是各自保存为业务结果记录。两张表都对
`pending_action_id` 建唯一约束。这是最后一道数据库级防线：即使上层代码发生重试，同一审批动作
也不能生成第二条退款或第二张券。金额在 Pydantic Schema 和服务层都限制为两位小数，防止
payload/result 按原 Decimal 计算、数据库却静默舍入成另一个值。

`refund_transactions.provider_reference` 和 `coupon_grants.code` 同样唯一，便于未来映射真实支付或
营销系统的幂等标识。

## 4. 请求阶段：首次确定性校验但不写业务数据

`ActionRequestService` 接收服务端构造的 `ToolContext`，模型参数 Schema 中没有 tenant、store、
customer、conversation 或 trace 字段。模型只能提供业务参数，无法把动作指向另一个顾客。

三类请求规则为：

### 4.1 取消订单

- 订单必须属于当前 tenant/store/customer；
- 只有 `pending` 或 `paid` 订单可以提出取消申请；
- 创建申请后订单状态保持不变。

### 4.2 退款

- 订单必须已经支付，且处于 `shipped` 或 `delivered`；
- 金额必须大于 0，且不能超过扣除历史成功退款后的剩余可退金额；
- 金额最多保留两位小数，禁止依赖数据库 `NUMERIC(12,2)` 静默舍入；
- 订单必须仍在 7 天退款窗口内；
- 支持一次订单分多次部分退款，但累计金额不能超过实付金额。

### 4.3 补偿券

- 金额必须大于 0 且不超过 50 元；
- 金额最多保留两位小数；
- 可以关联订单；如果提供订单号，同样强制验证归属；
- 券只在批准后写入 `coupon_grants`。

首次校验用于尽早拒绝明显无效请求，减少审批噪声。但它不能替代执行前复查，因为审批可能几秒、
几小时甚至几天后才发生。

## 5. 请求幂等：同一 Agent turn 的重复意图只创建一个动作

申请服务把以下字段做规范化 JSON，再计算 SHA-256：

```text
tenant_id
store_id
customer_id
conversation_id
trace_id
action_type
payload
```

哈希作为 `idempotency_key`，数据库对 `tenant_id + idempotency_key` 建唯一约束。相同 Agent
turn 内对完全相同动作发生工具重试时，服务返回已有 action，而不是继续堆积相同审批。

幂等边界不能只使用业务内容的永久哈希，否则顾客几天后合法地再次申请同额、同原因优惠券，也会
被错误识别成旧请求。当前把 `trace_id` 作为一次 Agent turn 的发生标识：同一 turn 的重试合并，
后续新 turn 的同内容申请会生成新的待审批动作。

并发请求可能同时执行“先查询、后插入”。创建逻辑用数据库 savepoint 包裹 insert；如果另一个
事务先写入相同唯一键，本事务只回滚 savepoint，再读取并返回胜出的 action，不会把整个会话事务
置为失败状态。

## 6. 批准阶段：锁、复查和一次执行

审批 API 从请求头构造独立的 `ApprovalContext`：

```text
X-Tenant-Id
X-Store-Id
X-Approver-Id
```

列表和详情查询始终过滤 tenant/store。访问其他店铺 action 时返回 404，而不是暴露“资源存在但
无权访问”。`X-Approver-Id` 在 Demo 中代表上游认证系统已经确认的审批人身份；生产环境应由
OIDC/JWT 和 RBAC 替代，不能信任公网客户端任意填写该请求头。

批准流程在同一事务内执行：

1. 使用 `SELECT ... FOR UPDATE` 锁定 `pending_action`；
2. 已经 `succeeded` 时直接返回原结果；
3. 非 `pending` 状态拒绝非法转换；
4. 记录审批人并进入 `executing`；
5. 用 action 中保存的可信 tenant/store/customer 锁定目标订单；
6. 重新检查订单状态、支付状态、退款窗口、剩余可退金额或券金额；
7. 写入业务结果和成功/失败审计记录；
8. 提交整个事务。

订单锁避免并发退款都读取到同一个“剩余可退金额”。action 锁则让并发批准串行化。状态幂等、
业务结果唯一约束和数据库锁形成三层保护，而不是只依赖前端禁用按钮。

如果订单在等待审批期间已经发货、取消、过期或退款余额不足，执行阶段会进入 `failed` 并记录稳定
错误码，不会强行使用申请时的旧结论。

## 7. 三种写操作的执行结果

### 7.1 取消订单

批准后再次确认状态为 `pending` 或 `paid`，然后把订单改为 `cancelled`。取消与退款仍是两种独立
动作：取消订单本身不会伪造支付退款记录。

### 7.2 退款

批准后创建唯一 `refund_transaction`，累计成功退款金额，并将支付状态更新为：

- 未全额退完：`partially_refunded`；
- 累计金额等于实付金额：`refunded`。

Demo 不连接真实支付渠道，`provider_reference` 是确定性生成的本地引用。接入真实支付 Provider
时，应把同一个 action 幂等键传给外部接口，并保存外部返回引用。

### 7.3 补偿券

批准后创建唯一 `coupon_grant`，券码由 action ID 稳定生成，例如 `CARE-...`。重复批准返回第一次
生成的结果，不会创建新券码。

## 8. API、错误语义与审批页面

新增接口：

```text
GET  /api/v1/approvals?status=pending
GET  /api/v1/approvals/{action_id}
POST /api/v1/approvals/{action_id}/approve
POST /api/v1/approvals/{action_id}/reject
```

错误状态区分为：

- 404：当前店铺作用域内找不到 action；
- 409：状态转换冲突，例如批准已拒绝动作；
- 422：申请参数或确定性业务规则不满足。

React 前端升级为人工审批控制台，支持：

- 选择 Demo 店铺；
- 输入审批人标识；
- 按待审批、已完成、已拒绝、失败或全部筛选；
- 查看动作类型、订单、顾客、金额、原因和 action ID；
- 批准或填写原因后拒绝；
- 查看结构化执行结果和完整审计时间线。

页面说明明确强调 Agent 只能提出请求，避免运营人员误以为聊天回复已经完成退款或取消。

## 9. Agent 路由与用户反馈

Mock Provider 新增退款、发券和取消意图，用于零 API Key 的可重复演示。路由顺序有一个关键
细节：明确的“政策、规则、时效”问题仍优先走知识检索，只有表达“帮我退款/取消/发券”的动作
意图才进入审批请求。

例如：

```text
“订单取消政策是什么？” -> search_store_policy
“帮我取消订单 AUR-...” -> request_order_cancellation
```

这延续了 Milestone 3 的修复：订单号只是实体，不决定工具；真正决定工具的是用户想查规则、查
状态，还是发起动作。

## 10. 自动测试与真实环境验证

新增测试覆盖：

- 创建取消申请后订单仍保持原状态；
- 拒绝动作永远不修改业务数据；
- 重复批准取消只产生一次状态变化；
- 重复批准退款只产生一条 `refund_transaction`；
- 重复批准发券只产生一条 `coupon_grant`；
- 部分退款后可以再次申请，但累计金额不能超过剩余额度；
- 其他店铺无法查看或批准 action；
- API 列表、详情、批准和重复批准；
- Agent 对话创建 pending action 并持久化完整工具链；
- 所有写工具 Schema 继续隐藏可信身份字段；
- 金额超过两位小数时 Schema 和服务层都拒绝；
- 同一 Agent turn 重试返回原 action，新 turn 的相同内容生成新 action；
- 7 工具只读 Registry 不暴露任何 action request 工具；
- 审计事件重新加载后仍按 `event_index` 稳定排序；
- `make reset-demo` 能清理已完成审批及其退款、优惠券记录。

真实 Docker/PostgreSQL 验证执行了完整取消链路：

```text
对话请求取消 AUR-202607-0001
  -> Agent 返回 pending action 71927089-f788-44b1-b1bf-93c81159c91d
  -> 批准前订单状态仍为 paid
  -> 审批 API 批准
  -> action 状态 succeeded，订单状态 cancelled
  -> 再次批准返回同一结果
  -> 审计记录为 requested / approved / execution_started / execution_succeeded
```

同时验证 Alembic 已到 `20260815_0004 (head)` 且没有 schema drift。审批控制台经过真实浏览器
验收，能显示已完成动作和 4 条审计事件，浏览器控制台无错误。

## 11. 面试时如何讲这一阶段

可以用下面这段话概括：

> Milestone 4 把 Agent 从只读问答扩展到安全写操作，但模型没有获得直接改数据库的能力。我把
> 每个写操作拆成 request 和 execute 两段：模型只能用服务端注入的可信上下文创建 pending
> action，订单、支付和优惠券在审批前完全不变。审批时数据库先锁 action，再按保存的
> tenant/store/customer 锁订单，并重新检查状态、退款窗口和剩余金额。重复批准由已成功状态直接
> 返回原结果，退款和券表还对 pending_action_id 建唯一约束。整个过程追加 requested、approved、
> execution_started 和 succeeded/failed 审计事件。因此安全性不是 Prompt 承诺，而是工具权限、
> 状态机、事务锁、唯一约束和自动测试共同保证。

如果面试官追问“为什么申请时校验过，批准时还要再校验”，回答重点是：审批存在时间差，申请时
可取消的订单可能已经发货，剩余退款额度也可能被其他流程消耗；执行必须基于最新数据库状态。

如果追问“前端按钮禁用是否足够防重复”，回答重点是：重复可能来自网络重试、两个审批人并发或
服务重启，前端不是一致性边界；必须依靠行锁、状态判断和数据库唯一约束。

如果追问“为什么不让 LLM 根据政策决定是否退款”，回答重点是：模型可以检索政策并提出建议，
但金额、归属、状态转换和有效期是确定性业务规则，应该由可测试的代码执行。

如果追问“为什么批准后立即执行，没有消息队列”，回答重点是：Demo 的目标是展示安全边界和状态
机，当前本地副作用足够快，因此使用同事务同步执行减少基础设施；未来接真实支付渠道时，可以把
`approved -> executing` 交给队列 worker，同时保留幂等键和状态机。

## 12. 当前限制与下一步

当前实现仍有明确边界：

- Demo 使用请求头模拟上游可信身份，没有实现登录、JWT、角色权限和双人复核；
- 退款和券是本地业务记录，没有调用真实支付或营销系统；
- 审批批准后同步执行，没有队列重试、退避和死信队列；
- 审计日志在业务数据库中追加保存，还不是外部不可篡改审计存储；
- Mock Provider 只覆盖确定性的演示意图，不代表生产模型的完整自然语言能力。

下一步进入 Milestone 5：建立 50～100 条离线评测集，量化工具选择、参数、安全、引用和任务成功
率；把现有 conversation、tool、approval、audit 和 trace_id 串成可视化时间线，并记录延迟、
token 和成本，使系统从“功能正确”继续走向“效果可测、问题可定位”。
