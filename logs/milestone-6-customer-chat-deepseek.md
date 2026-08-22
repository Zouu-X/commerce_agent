# Milestone 6：顾客聊天页与 DeepSeek V4 Flash 接入

## 1. 这一步解决什么问题

此前后端已经具备完整的 Agent Runtime、工具调用、RAG、审批和 Trace，但对招聘方来说仍有两个
明显断点：顾客只能通过 curl 发消息，商户后台和顾客入口混在同一种体验中；同时默认 Mock 虽然
适合确定性测试，却不能证明项目能够驱动真实大模型完成 tool-calling。

本轮补齐两个独立角色入口：

- `/` 是顾客聊天页，负责选择 Demo 店铺/顾客、输入问题和查看 Agent 回复；
- `/merchant` 是商户后台，负责审批敏感操作，并查看 Trace 与评测；
- 运行时默认 Provider 改为 `deepseek-v4-flash`；
- Mock Provider 不再承担产品演示，只用于单元测试和零成本回归基线。

## 2. 为什么仍保留 Provider 抽象

项目没有把 DeepSeek 请求散落到路由或业务服务里，而是在原有 `ModelProvider` 协议下增加
`DeepSeekProvider`。这样 Agent Runtime 仍只依赖统一的 `complete(messages, tools, timeout)` 契约，
模型厂商差异被限制在适配层。

DeepSeek V4 默认启用 thinking。thinking 模式下的多轮工具调用还要求把 `reasoning_content` 原样
回传；这个客服 Demo 更关注低延迟、可解释工具链，而不是展示长推理，因此适配层显式使用
non-thinking 模式。以后如果要比较 thinking 与 non-thinking，只需要扩展 Provider 消息结构和评测
配置，不需要改商品、订单、知识或审批服务。

## 3. API Key 如何处理

真实 Key 放在根目录 `.env.ds`，该文件被 `.gitignore` 排除，并由 Docker Compose 以 Secret 挂载到
`/run/secrets/deepseek_api_key`。后端启动时读取 Secret 文件，Key 不会：

- 被写进 Dockerfile 或镜像层；
- 进入前端构建产物或浏览器请求；
- 出现在 Git diff、README 或日志文档中。

为了兼顾本地调试，配置层仍支持直接传入 `MODEL_API_KEY`，但 Docker 默认走文件 Secret。测试覆盖
了纯 Key 文件、`MODEL_API_KEY=...` 文件和空环境变量回退到 Secret 文件三个边界。

## 4. 顾客聊天页的设计

聊天页刻意不做生产级登录系统，而是把 Demo 的安全边界讲清楚：用户可选择模拟店铺和顾客，
前端把三项身份作为可信请求上下文传给 API；工具 Schema 中没有 tenant、store 或 customer 字段，
模型无法自行覆盖身份。

页面展示：

- 顾客与 Agent 的消息气泡；
- 模型调用次数、工具调用次数和 token 用量；
- DeepSeek V4 Flash / non-thinking 运行状态；
- 前往商户后台查看 Trace 和审批的入口；
- 随当前顾客变化的“确定可用”问题示例。

### 客户输出安全边界

真实模型接入后发现，只靠 Prompt 会让模型偶尔把工具 JSON 直接翻译进回答，例如
`payment_status`、`has_shipment`、英文枚举、布尔值和 `citation_id`。这不是单纯的措辞问题，
而是内部事实表示和客户展示契约没有分层。

本轮新增独立的 presentation boundary：

1. 工具仍向 Trace Recorder 写入原始结构化结果，供商户审计和评测复盘；
2. Runtime 在把工具结果交回模型前，将状态和字段投影为中文业务摘要；
3. 知识来源拆成结构化 `sources`，客户只看到《资料标题》与版本，切片 ID 仅留在 Trace；
4. 最终回复在落库和返回前执行确定性过滤，拦截残留的内部字段、布尔值和机器引用；
5. 客户会话 API 隐藏 assistant tool-call 与 tool 消息，完整执行链只在商户 Trace 中查看。

这样做保留了 Demo 的可解释性，但把“对客户解释依据”和“对开发者暴露调试证据”分成两个不同
的接口契约。评测也相应从 Trace 判断 citation correctness，并增加客户展示安全检查，而不是继续
激励模型在自然语言中输出内部切片 ID。

示例没有硬编码一个全局订单号。API 会从当前数据库查询：

- 当前店铺中确实有库存的商品名称；
- 当前顾客拥有的订单；
- 当前顾客拥有且存在物流记录的订单；
- 当前顾客拥有且状态允许取消的订单。

这样切换顾客后，页面不会给出一个属于其他账号的订单号。验证时还发现“降噪耳机”并不是商品名
“降噪蓝牙耳机”的连续子串，会让严格关键词搜索返回空；最终商品示例直接使用数据库中的完整
商品名，避免把搜索策略的偶然行为伪装成可用样例。

## 5. 商户审批页如何与聊天协作

顾客请求取消订单时，DeepSeek 先查询订单，再调用 `request_order_cancellation`。这个工具只创建
`pending_action`，不会改订单。商户进入 `/merchant` 后才能点击“批准并执行”；审批服务会再次
校验状态，在事务中完成写入并记录审计事件。

这两个页面的职责边界是：

```text
顾客聊天页 -> DeepSeek 选择工具 -> 创建 pending_action
                                      |
                                      v
商户后台   -> 人工批准 + 状态复查 -> 执行业务写入 + Audit
```

## 6. 真实端到端验证

2026-08-16 使用 Docker Compose、真实 `deepseek-v4-flash` 和本地 PostgreSQL 完成了以下验证：

1. 商品问题“推荐有库存的降噪蓝牙耳机”调用商品搜索并回答 `降噪蓝牙耳机`、价格和库存；
2. 政策问题“无理由退货政策是多少天”调用知识检索，回答 7 天，并在客户页显示
   《七天/十五天无理由退货政策》· v1；原始切片 ID 仅保留在商户 Trace；
3. 当前顾客请求取消 `AUR-202607-0030`，Agent 查询订单后只创建待审批动作；
4. 审批前订单仍为 `pending`；
5. 在商户页面点击“批准并执行”后订单变为 `cancelled`；
6. 审计事件依次为 requested、approved、execution_started、execution_succeeded；
7. 浏览器从聊天页示例按钮发出政策问题，页面成功显示 DeepSeek 回复、结构化“回答依据”和本轮
   tool/token 元数据，未出现内部字段、英文状态、布尔值或原始 citation；
8. `/merchant` 可直接刷新，Nginx SPA fallback 正常；
9. `make up`、`make smoke` 均通过，结束后重置 Demo 数据。

静态与自动化检查：

- Ruff：通过；
- mypy：62 个源文件通过；
- pytest：74 passed；
- 前端运行时辅助逻辑：3 passed；
- ESLint：通过；
- TypeScript / Vite production build：通过；
- Docker Compose 配置与三个服务健康检查：通过。

## 7. 面试时如何讲这一阶段

可以这样概括：

> 我把原先 API 级的 Agent Demo 补成了双角色产品闭环。顾客在独立聊天页提问，真实 DeepSeek
> V4 Flash 只负责意图理解、工具选择和回答组织；事实必须来自受控工具。涉及取消、退款和发券时，
> Agent 只能创建待审批动作，商户在另一页面批准后业务状态才改变。示例问题也不是写死的，它会
> 根据当前顾客和数据库生成，避免演示时拿错订单或问到不存在的商品。Mock 仍然保留，但只作为
> 确定性回归基线。客户看到的是中文业务事实和可读资料标题，原始工具 JSON 与 citation 留在商户
> Trace，这样真实模型体验、可解释性和接口分层都能讲清楚。

## 8. 已知取舍

- 当前关闭 thinking 是为了降低交互延迟，并避免在未扩展消息协议前错误处理
  `reasoning_content`；
- 前端使用 URL 路径做最小双页面路由，没有引入 React Router，符合求职 Demo 的范围；
- Markdown 目前以普通文本显示；知识依据使用独立的结构化来源标签，不依赖富文本渲染；
- 真实模型的完整 60 条评测会产生 API 用量，`make eval-mock` 用于日常确定性回归；
- 公网部署、架构图、威胁模型和固定演示脚本仍属于后续 Milestone 6 包装工作。

## 9. Review 修复

- 切换店铺或顾客时同步清空输入框，避免把上一身份的订单问题带入新会话；
- Agent 失败时清除旧轮次信息并保留失败 Trace，可从顾客页直达商户 Trace；
- pytest 强制使用 Mock Provider，不继承本机真实模型配置；
- 前端读取服务端运行时信息，真实模型的 60 条完整评测会显示模型、单价和 API 用量确认。

## 10. 手动验收发现的退款闭环问题

手动演示“配送失败的键盘申请退款”时，DeepSeek 正确调用了 `request_refund`，但工具返回
`RETURN_WINDOW_EXPIRED`，商户后台没有待审批单。Trace 证明问题不在工具路由或审批页面，而在
业务时间语义：固定种子订单创建于 2026-07，校验却用真实系统时间并从订单创建日计算 7 天，导致
所有退款场景随着日历推进而失效。展示层又把错误统一写成“查询未完成”，进一步掩盖了真实原因。

修复后使用统一的退款窗口判定：

- 已签收订单从物流 `delivered` 事件起算 7 天；
- 配送失败意味着尚未开始签收后窗口，允许创建待人工审批申请；
- 商户批准时再次执行同一资格检查，审批前不改变支付状态；
- 普通超期、未付款、金额超限和订单状态错误会转换为客户可理解的原因；
- 60 条评测中的退款用例从“允许超期失败”改为“必须生成 pending action”；
- 新增 API 端到端测试，覆盖顾客退款、商户列表可见、批准执行和支付状态变更。

这个问题说明离线单元测试不能替代角色级手动验收。之前的成功退款测试注入了固定时钟，而评测
又把超期错误列为允许结果，因此两层测试都没有验证真正的“顾客请求 → 商户审批”业务目标。

## 11. 跨会话重复取消申请

继续手动验收时发现：顾客提交取消申请后刷新页面，新会话没有聊天记忆，因此再次取消同一订单会
生成第二条 pending action。根因是原幂等键包含 `conversation_id` 和 `trace_id`，它只能防止同一次
Agent turn 的重试，不能表达“这个订单已经有活动取消申请”的业务事实。

修复没有把长期业务状态塞进模型记忆，而是在 action service 查询同一 tenant、store、customer、
order 和 action type 下状态为 pending、approved 或 executing 的申请：

- 已存在时返回原 action，不插入新记录；
- 工具结果增加 `request_state=already_pending`；
- 客户展示层明确说明“申请已经提交、正在等待人工审批、无需重复申请”；
- rejected 和 succeeded 不属于活动申请，业务状态允许时可以重新提交；
- 新增跨两个 conversation 的服务测试和 API 角色级测试，断言商户列表始终只有一条记录。

这里刻意把“对话记忆”和“业务幂等”分开：聊天历史可以丢失或重建，但是否已经申请取消必须以
数据库中的审批状态为准。这个边界比依靠 LLM 记住前文更可靠，也更容易通过 Trace 解释。
