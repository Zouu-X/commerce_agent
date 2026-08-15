# Milestone 2：Agent Runtime 与安全只读工具

完成日期：2026-07-13

## 1. 里程碑目标

Milestone 2 的目标是把 Milestone 1 的确定性电商服务接入一个真正的 tool-calling Agent，形成第一条完整业务闭环：

```text
用户消息
  -> 模型判断是否需要工具
  -> 应用校验工具名和参数
  -> 服务端注入可信身份
  -> 只读业务服务查询 PostgreSQL
  -> 工具结果返回模型
  -> 模型生成最终答复
  -> 完整消息链写入数据库
```

完成后，Agent 可以端到端处理：

- 商品搜索和有库存推荐；
- 当前顾客的订单列表与订单详情；
- 物流轨迹和“5 天未更新”“配送失败”异常；
- 当前顾客的售后申请状态。

这一阶段仍不实现政策知识检索、退款、取消订单或发券。知识检索属于 Milestone 3；有副作用的操作必须等 Milestone 4 的人工审批、幂等和审计机制完成后才能开放。这样可以保持读写边界清楚，避免为了演示聊天而提前加入不安全的写能力。

## 2. 为什么不用 Agent 框架

本项目实现了一个精简的自研 tool-calling loop，没有使用 LangChain 等编排框架。

原因不是框架不好，而是这个求职 Demo 需要清楚展示以下能力：

- 模型消息如何转换；
- 工具 Schema 如何生成和校验；
- `tool_call_id` 如何关联 assistant 和 tool 消息；
- tenant/customer 身份在哪一层注入；
- 循环、工具次数和超时如何限制；
- 工具错误如何安全返回模型；
- 会话历史如何持久化和重新加载。

如果这些逻辑全部隐藏在框架中，面试时很难证明自己理解 Agent 的实际运行边界。当前 Runtime 代码规模可控，也为后续审批、trace 和评测保留了明确扩展点。

## 3. Provider 抽象

### 3.1 供应商无关的数据结构

Agent 核心只认识以下内部类型：

- `ProviderMessage`：system、user、assistant 或 tool 消息；
- `ToolSpec`：工具名、描述和 JSON Schema；
- `ToolCall`：调用 ID、工具名和结构化参数；
- `ModelResponse`：文本、工具调用和 token usage；
- `ModelProvider`：异步 `complete()` 协议。

Runtime 不导入 OpenAI SDK，也不读取供应商响应字典。以后切换其他兼容供应商时，只需替换 Provider adapter，不需要改工具或业务服务。

### 3.2 OpenAI-compatible Provider

`OpenAICompatibleProvider` 使用 `POST /chat/completions` 风格的协议：

- 把内部消息转换成 Provider 的 `messages`；
- 把 Tool Registry 转成 `tools`；
- 保留 assistant 返回的 `tool_calls`；
- 将工具结果通过相同 `tool_call_id` 回填；
- 解析字符串形式的 function arguments；
- 记录输入和输出 token；
- 将 HTTP、JSON 和响应 Schema 错误统一转换成 `model_provider_failed`。

这里仍然不信任模型参数。即使供应商支持结构化工具调用，模型仍可能生成非法 JSON、未知工具、缺失参数或额外参数，所以应用端必须再次校验。

### 3.3 为什么默认使用 Mock Provider

项目默认 `MODEL_PROVIDER=mock`。Mock Provider 是一个确定性的本地模型替身，它能识别 Demo 中的商品、订单、物流和售后意图，然后生成真实 ToolCall。

这样设计有三个目的：

1. 招聘方不需要 API Key 就能一键运行 Demo；
2. CI 和端到端测试不会受模型随机性、网络、限流或费用影响；
3. 可以把“Agent 编排是否正确”和“某个模型表现如何”分开测试。

Mock 不直接读取数据库，也不伪造价格、库存或订单状态。它只能选择工具和根据工具结果组织回复，因此业务事实仍然来自 PostgreSQL。

使用真实模型时配置：

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_NAME=<provider-model-name>
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_API_KEY=<provider-api-key>
```

## 4. ToolContext：模型不能填写身份

本阶段新建了不可变的：

```python
@dataclass(frozen=True)
class ToolContext:
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID
    conversation_id: UUID
    trace_id: UUID
```

模型可见的工具参数只包含关键词、分类、商品 ID、订单号、售后 ID 和 limit 等业务字段。以下身份字段完全不在 JSON Schema 中：

```text
tenant_id
store_id
customer_id
conversation_id
trace_id
```

`ToolContext` 由 FastAPI 请求头解析出的可信 `CommerceContext`、当前数据库会话 ID 和本次请求生成的 trace ID 组合而成。

这是本阶段最重要的安全设计。不能让模型通过类似下面的参数查询他人数据：

```json
{
  "order_number": "AUR-202607-0001",
  "customer_id": "另一个顾客"
}
```

所有工具参数模型都设置 `extra="forbid"`。如果模型擅自加入 `customer_id`，Registry 返回稳定的 `invalid_arguments`，不会把它传入业务服务。

## 5. Tool Registry

Tool Registry 统一保存每个工具的：

- 名称；
- 给模型看的说明；
- Pydantic 参数模型；
- 异步执行函数。

它承担四项职责：

1. 从 Pydantic 模型生成工具 JSON Schema；
2. 拒绝未知工具；
3. 严格校验参数并禁止额外字段；
4. 将业务异常转换为稳定、可给模型消费的结果。

成功结果统一为：

```json
{
  "ok": true,
  "tool": "get_order_details",
  "data": {}
}
```

错误结果统一为：

```json
{
  "ok": false,
  "error": {"code": "order_not_found"}
}
```

数据库异常和 Python traceback 不会直接进入模型上下文或返回给用户。另一顾客的订单与不存在的订单使用相同 `order_not_found`，避免泄露订单是否存在。

## 6. 六个只读工具

### 6.1 `search_products`

按关键词、分类、库存、最高价格和数量限制搜索当前店铺商品。返回商品 ID、名称、描述、分类、最低 SKU 价格和合计库存。

推荐时默认查询有库存商品，避免 Agent 推荐已售罄商品。

### 6.2 `get_product_details`

按商品 UUID 返回商品描述及全部 SKU 的属性、价格和库存。服务层继续强制 tenant/store 过滤。

### 6.3 `get_customer_orders`

返回当前顾客在当前店铺的订单列表。工具没有 customer 参数，顾客身份只能来自 ToolContext。

### 6.4 `get_order_details`

按订单号返回订单状态、支付状态、实付金额、订单项、是否有物流和售后 ID。查询条件仍然同时匹配 tenant、store 和 customer。

### 6.5 `track_shipment`

按订单号返回承运商、运单号、状态、最后更新时间和倒序物流节点，并复用 Milestone 1 的确定性规则识别：

- `NO_UPDATE_5_DAYS`；
- `DELIVERY_FAILED`。

### 6.6 `get_after_sale_status`

按售后 UUID 返回当前顾客的申请类型、原因、金额和状态，同时检查 tenant/customer 和关联订单的 store。

## 7. Agent Runtime 循环

每次用户请求执行以下步骤：

1. 通过 tenant/store/customer/conversation 四项条件加载会话；
2. 读取最近的持久化消息；
3. 写入新的 user 消息；
4. 添加系统安全指令；
5. 调用 Provider；
6. 如果模型返回工具调用，先检查工具总预算；
7. 保存带 `tool_calls` 的 assistant 消息；
8. 并发执行同一轮互不依赖的只读工具；
9. 保存每条 tool 消息及对应 `tool_call_id`；
10. 把结构化结果返回模型；
11. 模型无工具调用时保存最终 assistant 回复并结束。

目前配置的安全上限为：

| 限制 | 默认值 |
|---|---:|
| 最大模型循环 | 6 |
| 最大工具调用 | 8 |
| 模型单次超时 | 30 秒 |
| 工具单次超时 | 10 秒 |
| 请求总超时 | 45 秒 |
| 加载历史消息 | 最近 50 条 |
| 单条工具结果 | 最大 12,000 字符 |

达到工具预算返回 `agent_tool_call_limit_exceeded`；达到循环预算返回 `agent_model_loop_limit_exceeded`；模型或请求超时分别返回稳定超时码。

只读工具可以并发，因为它们不改变业务状态。有副作用工具在 Milestone 4 接入后不能沿用这一并发策略，必须进入审批状态机顺序执行。

## 8. 会话和消息持久化

新增 Alembic revision：

```text
20260713_0002
```

新增两张表：

```text
conversations
  └── messages
```

### 8.1 conversations

会话直接保存 tenant、store 和 customer，形成明确隔离边界。读取会话时必须同时匹配：

```text
conversation_id + tenant_id + store_id + customer_id
```

因此即使另一顾客拿到 conversation UUID，API 也只返回 `conversation_not_found`。

### 8.2 messages

消息保存：

- 单会话递增 sequence；
- role；
- content；
- tool_call_id；
- tool_name；
- tool_calls_json；
- created_at。

数据库约束确保 role 只能是 user、assistant、tool，sequence 必须大于 0，且同一会话 sequence 唯一。

一次标准工具调用会保存四条消息：

```text
1 user       用户问题
2 assistant  content 为空，保存 tool_calls
3 tool       保存结构化结果和 tool_call_id
4 assistant  基于结果生成的最终答复
```

保存 assistant 的 `tool_calls` 很重要。只保存最终文本会让下一轮无法恢复 Provider 所需的合法工具消息链，也无法在后续控制台展示模型实际选择过什么工具。

发送消息的 API 在整轮 Agent 成功后统一 commit。如果模型超时或超过预算，请求事务会 rollback，不留下半条消息链。

## 9. API

新增接口：

```text
POST /api/v1/conversations
GET  /api/v1/conversations/{conversation_id}
POST /api/v1/conversations/{conversation_id}/messages
```

创建和读取会话都要求三个可信身份 Header。发送消息响应包括：

- 本轮最终 assistant 消息；
- trace ID；
- 模型循环次数；
- 工具调用次数；
- Provider 返回的 input/output token。

Mock Provider 的 token 为 0；真实 compatible Provider 会回填 usage。完整 trace 时间线和审计日志将在 Milestone 5 持久化。

## 10. 测试策略

后端测试从 9 个增加到 19 个，新增覆盖：

- 工具 Schema 不暴露可信身份；
- 模型额外提交 customer_id 会被拒绝；
- 未知工具返回稳定错误；
- Registry 层跨顾客订单查询返回 not-found；
- Runtime 工具调用预算；
- Runtime 模型超时；
- 失败请求 rollback，不保存半条消息链；
- 商品咨询端到端流程；
- 订单查询端到端流程；
- 物流 5 天未更新端到端流程；
- user/assistant/tool/assistant 完整持久化；
- tool_call_id 前后对应；
- 会话跨顾客不可访问；
- Agent 跨顾客订单查询不泄露金额或状态。

自动检查结果：

```text
Ruff: 通过
mypy --strict: 32 个源文件通过
pytest: 19 passed
```

## 11. 真实 PostgreSQL 验收

除了 SQLite 内存测试，还执行了 Docker Compose 真实验收。

### 11.1 迁移

已有 Milestone 1 数据卷成功执行：

```text
20260712_0001 -> 20260713_0002
```

`alembic current` 返回 `20260713_0002 (head)`，`alembic check` 返回：

```text
No new upgrade operations detected.
```

### 11.2 商品和多轮会话

同一个会话先询问“请推荐有库存的降噪耳机”，Agent 调用 `search_products`，返回真实结果：

```text
降噪蓝牙耳机，最低 ¥83.00，库存 21
```

随后询问 `AUR-202607-0001`，Agent 调用 `get_order_details`。数据库读取到 8 条按 sequence 排列的消息，证明两个 turn 的 user、tool call、tool result 和 final assistant 都已保存。

### 11.3 物流异常

订单所属顾客询问：

```text
订单 AUR-202607-0005的物流怎么还没更新？
```

Agent 正确调用 `track_shipment` 并回复：

```text
物流状态为 shipped；物流已超过 5 天未更新。最近节点：运输途中。
```

这里也验证了订单号紧邻中文字符时仍能被 Mock Provider 正确提取。

### 11.4 跨顾客安全

另一顾客在自己的会话中查询 `AUR-202607-0001`，HTTP 请求本身成功完成，但工具结果为 `order_not_found`，最终只回复：

```text
当前账号下未找到对应记录，请核对信息后再试。
```

回复没有金额、状态、商品或订单归属信息。另一顾客直接读取原顾客 conversation ID 时得到 HTTP 404。

## 12. 面试时如何讲这一阶段

可以按以下顺序说明：

1. **先讲边界**：Milestone 1 已经保证业务事实和租户过滤，本阶段只让模型负责意图理解、选工具和组织回复。
2. **再讲解耦**：用 Provider Protocol 隔离模型供应商；默认 Mock 保证零 Key 和可重复测试，真实模型走 compatible adapter。
3. **强调安全**：身份不进入工具 Schema，模型只能填订单号等业务参数；ToolContext 由服务端注入，Pydantic 禁止额外字段。
4. **讲 Runtime**：自研循环保存 tool_calls，通过 tool_call_id 回填结果，支持只读并发，并设置模型循环、工具数量和三层超时。
5. **讲持久化**：不是只保存最终聊天文本，而是完整保存 user、assistant(tool_calls)、tool、assistant，下一轮可恢复，也能支持未来 trace 控制台。
6. **用证据收尾**：18 个自动化测试、真实 PostgreSQL migration/check、三条端到端流程和跨顾客无泄漏验证。

一句话总结：

> 我把确定性电商服务封装成身份不可伪造的只读工具，并实现了一个供应商无关、受预算和超时保护、可完整恢复消息链的 tool-calling Agent Runtime；默认 Mock 让 Demo 零配置可复现，真实模型只需替换 Provider。

## 13. 当前限制与下一步

当前前端仍是工程基线页，Agent 通过 Swagger 或 curl 演示；顾客聊天页和控制台会在后续里程碑逐步完善。

当前 Agent 只能依据结构化电商数据回答。退换货政策、发货时效和补偿规则还没有检索证据，不能把它们写进 Prompt 让模型记忆。Milestone 3 将新增：

- 店铺政策文档和 chunk；
- 关键词与向量混合检索；
- 有效期和店铺隔离；
- 答复引用；
- Prompt injection 测试。

## 14. 后续修正（2026-07-14）

- 同一轮的多个只读工具改为按模型返回顺序执行，避免多个任务并发共享同一个 `AsyncSession`，并补充顺序执行回归测试。
- OpenAI-compatible Provider 的 HTTP 超时统一映射为 `model_timeout`，避免同类超时在 502 和 504 之间产生不一致。
