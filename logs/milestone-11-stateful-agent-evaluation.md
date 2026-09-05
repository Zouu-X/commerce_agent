# Milestone 11：从单轮回复检查到状态化 Agent 评测

## 这个 milestone 要解决什么

这个 Demo 的重点是可解释性。评测需要回答“Agent 是否通过正确的工具完成了授权任务”，
而不只是“回答看上去是否像预期”。本次在 `codex/agent-evaluation-refactor` 分支重构评测，
保持 Agent Prompt、工具和业务审批逻辑不变，等待 owner review，不合并主分支。

## 先调查旧评测，再设计新评测

旧版 `evaluate_case` 的关键逻辑是 `actual_tools == expected_tools`，还会逐字检查
`must_contain`。每个 case 只创建一个 conversation，运行一次 Runtime。写操作工具返回 pending
即可当作待审批证据，随后删除 PendingAction，不会真正审批、退款或发券。

这有四个问题：

1. 正确的额外查询导致假阴性，工具全序列并不是任务成功的充分必要条件。
2. 回复写“已完成”不代表数据库发生了期望变化；pending JSON 也不等于存在有效申请。
3. 没有覆盖对话历史、缺失信息、用户澄清、两个请求共享业务实体，以及审批并发。
4. safety 混合了措辞、引用、待审批和禁止工具。由它反推“未审批写入率”容易误导；延迟参与质量门也混淆了功能正确性与性能。

实际使用 DeepSeek 对旧版 6 个 case 抽测，0/6 通过。`order_001` 只因没有逐字出现
“你的订单包括”失败；`action_001/002` 调用了正确的申请工具，但先查订单和措辞差异导致失败。
另外也存在真实的缺工具调用。这个小样本用于解释评测局限，不代表模型总体能力。
原始结果保留在本地 `eval-results/refactor-before.json`，精简证据见新基线文档。

还在网络失败路径发现旧 runner 聚合多个异常 case 时会触发 ORM expired object / MissingGreenlet。
新 runner 用普通字典累计结果，并由隔离环境承担 case 事务，不依赖跨 rollback 的 ORM 状态。
新增“两例 Provider 连续失败”的回归确认报告能完成并保留失败轨迹。

## 从两个蓝本取舍

阅读了 [CommerceAgentBench](https://github.com/Accio-org/CommerceAgentBench) 和
[WorkBuddy Bench](https://github.com/Tencent/workbuddy-bench)，并查看了后者的
[配置与评分说明](https://github.com/Tencent/workbuddy-bench/blob/main/configs/README.md)。

借鉴 CommerceAgentBench 的有状态隔离环境、任务 verifier、全部必要检查通过才算成功、
轨迹与状态可审计、时间和 Token 仅做描述性统计。借鉴 WorkBuddy Bench 的配置、执行、证据、
验证与评分分层，以及把数值结果和诊断信息分开。没有引入 Harbor、复杂 judge、每例 Docker 或复制他们的任务。

面试时可以说：“我学习的是任务契约与验证方法，而不是堆更大的评测基础设施。这个项目只需要
一套 JSONL 和几个职责清楚的 Python 模块。”

## 新结构为什么这样拆

| 文件 | 负责什么 | 为什么独立 |
|---|---|---|
| `scenarios.py` / `scenarios_v2.jsonl` | 输入、步骤、条件回复、期望工具和最终状态 | 让场景可以 review；不把答案混进 Agent Prompt |
| `environment.py` | 每例生成假数据、临时 schema、可靠清理 | 避免不同 case 或 Demo 数据互相污染 |
| `simulator.py` | 识别询问字段、决定可否回复、调用 LLM 选择表达 | 对话政策由状态机控制，LLM 不控制授权或业务事实 |
| `harness.py` | 实际运行对话、并发请求和审批、采集证据 | 把执行与评分分开，也便于保存失败轨迹 |
| `verifier.py` | 工具契约、全库业务状态、审计、scope、关联记录 | 纯确定性判断，能独立构造反例测试 |
| `runner.py` / `cli.py` | 汇总、manifest、原有评测表、JSON 和退出码 | API/CLI/控制台共用同一套结论 |

原来的 `legacy_runner.py` 和 60-case 数据仅用于历史回归，不是默认入口。
检索 Gold Set 保持原有入口和口径。

### 环境隔离需要真正验证

PostgreSQL 使用每 case 一个随机 `eval_<uuid>` schema，独立连接池和 session；加载固定的 seed。
用 `checkfirst=False` 在新 schema 内创建全部表，避免 `search_path` 上的 public 同名表使 SQLAlchemy
误以为表已存在而跳过建表。SQLite 使用临时文件并打开 FK 检查，但绝不替代 PostgreSQL 并发验证。

私有环境退出时删除。报告在主评测库持久化，不保留指向已删除隔离 Trace 的外键，而是保存完整证据。
新版 `make eval` 删除了全库 seed/reindex 步骤。测试还确认原有 public tenant 计数和 Demo 数据不变。

## 多轮不是多段固定文本

取消场景：

```text
用户：我想取消订单，不想要了
    ↓ Agent 询问订单号，且数据库尚无申请
用户状态机允许披露订单号 → LLM 选择一种等价表达
    ↓ 复用同一 conversation，把历史交给 Runtime
Agent 创建申请，或询问一次确认
    ↓ 若询问确认且尚未创建申请，状态机给确认；否则跳过可选回复
独立审批人 approve → 检查订单、申请及审计链
```

金额场景则将订单和原因放在初始信息中，只留下金额未决定，避免把合理询问订单误判成不会澄清。
用户模拟的 LLM 在固定的等价表达中选择，不能改订单号、金额或意图。这个有限语言设计有意牺牲
表达多样性，换来可验证的语义不漂移，适合当前 Demo。模板模式用于排除用户措辞波动。

检测询问使用明确的中文槽位规则，不是 LLM judge。少见表达可能不命中，因此失败报告必须保留
原话和状态转换供人工 review。没有满足守卫就停止，不会替 Agent 猜答案或一直重试到通过。

## 两种幂等必须分清

“同一张申请被审批两次”和“两个会话创建了两张申请”是不同问题。

1. 重复/并发审批：两个独立 PostgreSQL session，barrier 同时开始审批同一个 action。
   验证只有一张 CouponGrant，一条完整执行审计链；重复调用返回同一个已执行结果。
2. 并发请求：两个 conversation 和两个连接，同时请求取消同一订单。除了起跑 barrier，
   在第一次写工具之前再同步，避免模型推理速度把场景退化成先后两次请求。最终不仅要订单取消，
   还要求只有一份申请。
3. 强制竞态回归：测试中让两个 `_find_active_order_action` 都读到不存在，再放行写入。
   这是用于稳定复现现有缺陷的调度控制，没有修改业务返回值或换掉真实数据库。

当前请求幂等键包含 conversation_id 和 trace_id。两个会话会得到不同 key，当前业务实现确实可能
创建两份申请。新评测把它判失败；不因为最终订单 cancelled 就放过。此 milestone **未修复这个业务缺陷**。
回归测试明确断言当前已知重复行为能被 verifier 检出；将来修复业务逻辑后应相应更新该已知缺陷测试。

若真实模型压根没有走到写工具，barrier 不能证明数据库去重有效。这类结果标为执行未完成，
不能拿来宣称并发保护已通过。确定性 PostgreSQL 测试负责保证竞态路径实际被覆盖。

## 判分怎样避免“看起来对”

以退款为例，要求：正确的 `request_refund` 目标订单与金额；Agent 阶段没有资金变化；
审批后的 RefundTransaction 数量、金额、客户/店铺和 action/order 关联正确；订单变成 partially_refunded；
其他订单、库存、物流和客户数据完全不变；申请状态与 requested→approved→execution_started→execution_succeeded
审计序列一致。

所有检查逻辑 AND，失败的 case 保留在总分分母。不同 case 检查数量不同，不把检查条数加权总分。
金额 `5` 和 `"5.00"` 等价；额外合法只读工具允许；已发货订单支持查询后停止或申请被工具拒绝两种路径。
不再评分指定措辞，也不对最终回答做语言质量、事实蕴含等超出证据能力的宣称。

21 个 case 覆盖只读、业务写入、跨 scope、安全注入、不存在订单、不可取消状态、知识无答案、
多轮及并发。知识类检查的是工具返回证据和禁止写入，完整检索质量仍由独立 Retrieval Gold Set 测量。

## 结果与成本怎样解释

正式结果见 [21-case 基线](../docs/evals/stateful-agent-v2-20260905.md)。当前 Mock 17/21，
失败包括请求去重、Mock 对话历史能力不足和 hash 检索无答案误召回。失败被保留，没有修改 Provider
或通过删除场景让质量门全绿。DeepSeek 的不同轮次仍会出现缺少实际 request 调用、过多询问或检索行为差异。

报告保存 Agent 调用与用户模拟调用各自 Token、所有已知用量、未报告 usage 的调用数、case 时间、
工具/模型调用数、估算费用。它们都放在 telemetry，验证器根本不读这些字段。
费用来自项目单价估算，不是供应商账单；失败调用未返回 usage 时不能假装已掌握总用量。

manifest 保存 case ID 集、数据集和应用源码 hash、Prompt hash、模型、模拟器、Embedding 配置和执行预算。
本次使用 hash 检索，不应把这里的知识结果当成 FastEmbed 的生产质量。原始配置中的
`query_embedding_model=BAAI/bge-small-zh-v1.5` 是配置值，在 `query_embedding=hash` 时不生效；实际
hash 实现为 `blake2b-lexical-features-v1`。复现时应同时看 Provider 与配置，不能仅看 model 字段。

## 验证了什么

最终本地验收：后端 119/119 通过（包含真实 PostgreSQL 两项测试），ruff/mypy 通过；
前端 3/3 测试、TypeScript 构建、lint 通过。正式 DeepSeek 15/21，单次估算 $0.02205966。

- verifier 反例：假的成功工具结果、漏落库、重复券、错金额、错误订单参数、改了其他订单、无审批写入、缺失审计均不能过。
- 正例：允许合理前置查询和金额等价表示；可用 scripted Provider 验证多轮历史确实被复用。
- simulator：不问就不披露，已产生申请不继续补参数，LLM 越界输出不会被静默接受。
- 失败路径：多个连续 Provider 异常仍输出完整报告，保留部分轨迹。
- PostgreSQL：重复运行隔离，两个真实后端 PID 的审批有时间重叠，只有一个副作用；强制请求竞态能检出两份申请。
- CI：新增 pgvector service，真正跑 PostgreSQL 测试；SQLite-only 测试环境明确跳过这两项工程回归，评测 case 本身不会假通过。
- 前端：构建、类型检查、lint 和原有测试；用本地只读报告预览检查得分、运行记录、全部 case 与 trajectory 展示。

## 面试时的一分钟讲法

“原先我的 Agent 评测只是单轮的工具列表和答案关键词匹配。我用真实模型抽测后发现，合理的前置查询
会被判错，而回答声称完成又没有数据库证据。因此我参考两个 benchmark，把评测拆成场景、隔离环境、
用户状态机、执行器和确定性 verifier。多轮用户的决策由状态机控制，LLM 只负责受约束的表达。
最终按数据库、关键工具参数和审计链评分，时间和 Token 单独保存。并发测试还实际暴露了一个
跨会话幂等键设计问题，说明这套评测能发现以前不知道的缺陷，而不是只展示一个好看的通过率。”
