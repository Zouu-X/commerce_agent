# Milestone 7：RAG Retrieval Gold Set 与独立检索评测

完成日期：2026-08-22

## 1. 为什么在接入真实 Embedding 前先做 Gold Set

Milestone 3 已经实现了按 tenant/store/有效期过滤的混合检索，但当时的验证主要是少量功能测试和
Agent 端到端用例。Milestone 5 的评测发现两个知识回答夹带了错误引用，证明现有检索存在噪声；
但端到端指标同时受到工具路由、Prompt、模型生成和客户展示层影响，无法单独回答以下问题：

1. 正确文档有没有进入 Top-K；
2. 正确文档是否排在第一位；
3. 多意图问题是否召回了全部必要证据；
4. 近似政策是否作为 hard negative 混入；
5. 无答案问题是否被错误匹配；
6. 真实 Embedding 或 reranker 相比当前方案究竟提升了什么。

因此本阶段没有先调阈值或更换向量模型，而是先建立一套独立、固定、可人工审查的 Retrieval
Gold Set。后续所有检索方案必须在同一份数据上 A/B，避免凭几个演示问题主观判断效果。

## 2. 数据集设计

新增数据集：

```text
backend/app/evaluations/data/retrieval_gold_v1.jsonl
```

第一版包含 100 条查询，固定拆分为：

- `dev`：75 条，用于观察失败样本和调整检索策略；
- `holdout`：25 条，作为冻结的回归集合，避免只对公开调参样本优化。

同一个用例包含：

- `case_id`、`split`、查询文本和 tenant；
- 可选的 `document_type` 和历史查询时间 `as_of`；
- 1～3 级的相关来源及等级；
- 已知 `negative_sources`；
- 绝不能出现的 `forbidden_sources`；
- 店铺差异所需的正文片段 `must_contain`；
- `tags` 和逐条记录的 `annotation_reason`。

示意：

```json
{
  "case_id": "refund_001",
  "query": "退款多久到账？",
  "relevance": {"refund-timing:v1": 3},
  "negative_sources": ["quality-return:v1", "no-reason-return:v1"],
  "tags": ["exact", "hard_negative"],
  "annotation_reason": "询问退款资金到账时间，不是在询问能否退货。"
}
```

### 2.1 为什么标签使用 `source_key:version`

原 Agent 评测使用完整 `citation_id`，例如 `refund-timing:v1#chunk-1`。这种格式适合运行时精确
追踪，但 chunk 编号会随切片策略变化。Gold Set 的目标是长期比较不同 chunker、Embedding 和
reranker，因此相关性标签稳定在：

```text
source_key:version
```

Runner 会把多个 chunk 去重到文档版本后计算指标，同时在报告中保留原始 chunk citation，既保证
标签稳定，也不丢失故障诊断信息。

### 2.2 覆盖范围

100 条用例覆盖当前 13 类有效知识和一个历史版本，包括：

- 无理由退货、质量问题退换、发货时效；
- 物流停滞、配送失败、延迟补偿；
- 保价、订单取消、退款到账、换货、质保；
- 商品清洁保养和知识内容安全说明；
- 当前 v1 和历史 v0 的时间点检索。

表达方式覆盖精确问法、口语改写、隐含意图、条件与例外、订单号噪声、多意图、跨文档类型、
Prompt Injection 和 OOD。81 条带有明确 hard negative，重点区分：

- 退款到账 vs 质量退货 vs 无理由退货；
- 配送失败 vs 物流停滞 vs 延迟补偿；
- 发货时效 vs 物流状态 vs 取消订单；
- 保价补差 vs 延迟补偿券；
- 质量退换 vs 换货流程 vs 质保。

查询改写可以由模型辅助产生候选，但 Gold 标签和解释不能来自当前检索输出，必须以知识原文为准
由项目 owner 确认，否则会形成系统用自己的答案证明自己正确的循环。本版根据 seed 原文逐条整理，
合并前仍应由 owner 抽检 dev，并完整确认 25 条冻结 holdout。

## 3. 独立 Runner

新增：

```text
backend/app/evaluations/retrieval_dataset.py
backend/app/evaluations/retrieval_runner.py
backend/app/evaluations/retrieval_cli.py
```

Runner 直接调用：

```text
KnowledgeSearchService.search()
```

它不经过 Agent Runtime、工具选择、系统 Prompt 或生成模型，因此失败可以明确归因到检索层。
对每条命中还会根据确定性 seed ID 验证 document 是否真的属于目标 tenant，解决两个店铺具有相同
`source_key:version` 时仅看 citation 无法发现跨店铺泄漏的问题。

回答型用例的单例通过条件为：

1. 第一个结果是相关来源；
2. 所有标注的相关来源在 Top-3 内；
3. 不命中明确 hard negative；
4. 不命中过期或时间无效的 forbidden source；
5. 没有跨 scope document；
6. 店铺差异正文满足 `must_contain`。

无答案用例只有返回空列表才通过。

## 4. 指标

报告输出：

- Recall@1、Recall@3、Recall@5；
- Precision@3；
- MRR；
- 支持 1～3 级相关性的 nDCG@5；
- 无答案准确率和误召回率；
- hard-negative case 命中率；
- forbidden source 命中率；
- scope violation rate；
- 正文约束失败率和 P95 延迟。

质量门禁为：

```text
Recall@3 >= 95%
MRR >= 90%
nDCG@5 >= 95%
无答案误召回率 <= 5%
forbidden source 命中率 = 0
scope violation rate = 0
```

报告同时记录数据集版本、retrieval config、RRF 参数、阈值、Embedding provider/model 和向量维度。
全量运行会同时输出 dev/holdout 子集指标；每条失败保存命中 citation、RRF 分数、缺失来源、
hard negative 和明确失败代码，方便从聚合数字直接下钻到样本。
真实 Embedding 接入后，不能只说“换了模型”，而要用同一份 Gold Set 展示指标和失败样本的变化。

## 5. 当前哈希混合检索基线

在独立 Docker 项目中使用 PostgreSQL 17 + pgvector 0.8.2、真实 `ts_rank_cd` 和 HNSW 路径运行
100 条用例，结果为：

| 指标 | PostgreSQL 基线 |
|---|---:|
| 用例通过率 | 83 / 100 |
| Recall@1 | 89.78% |
| Recall@3 | 91.40% |
| Recall@5 | 91.40% |
| Precision@3 | 87.99% |
| MRR | 94.09% |
| nDCG@5 | 92.11% |
| 无答案准确率 | 85.71% |
| 无答案误召回率 | 14.29% |
| hard-negative case 命中率 | 6.17% |
| forbidden source 命中率 | 0% |
| scope violation rate | 0% |
| P95 检索延迟 | 3 ms |

固定拆分的子集结果为：

| 子集 | Pass rate | Recall@3 | MRR | nDCG@5 | 无答案误召回率 |
|---|---:|---:|---:|---:|---:|
| dev（75） | 80.00% | 90.71% | 93.57% | 91.49% | 20.00% |
| holdout（25） | 92.00% | 93.48% | 95.65% | 93.97% | 0% |

holdout 分数更高不代表方案泛化优于 dev，只说明第一版固定样本的难度分布不同。后续不能为了让
两组数字接近而移动样本；调参只观察 dev，方案固定后再查看 holdout。

SQLite fallback 得到完全相同的质量指标，P95 为 1 ms。这说明当前 28 个小型切片上的两条方言
路径结果一致；它不等同于大规模 HNSW 召回验证，后续仍需要扩大语料后检查执行计划和过滤召回。

质量门禁按预期没有通过：MRR、版本安全和 scope 安全已经达标，但 Recall@3、nDCG@5 和无答案
误召回率没有达到目标。Gold Set 的价值就是如实暴露这些差距，而不是为了让第一版得到满分。

### 5.1 17 条失败反映出的模式

17 条失败可以归纳为：

1. 隐含或对比表达漏召回，例如“不喜欢但没坏”“商品有瑕疵”“包裹没有新节点”；
2. 宽泛概念造成尾部噪声，例如退款到账同时返回质量退货；
3. 多意图只召回其中一份证据，例如发货时效加延迟补偿、质保加保养；
4. 条件性补偿问题被误召回为保价；
5. “写一首关于夏天的诗”因短字符哈希碰撞错误命中保价政策。

这组结果也证明当前 64 维哈希向量不是独立的真实语义通道。它适合确定性 CI，但不能作为 RAG
效果的最终演示方案。

## 6. 使用方法

运行全部 Gold Set：

```bash
make eval-retrieval
```

只运行调参或冻结集合：

```bash
make eval-retrieval RETRIEVAL_SPLIT=dev
make eval-retrieval RETRIEVAL_SPLIT=holdout
```

命令会重置为确定性 Demo 数据，报告写入：

```text
eval-results/retrieval-evaluation-<timestamp>.json
eval-results/retrieval-latest.json
```

JSON 报告目录已被 Git 忽略，避免把每次本地运行的大型结果提交到仓库；数据集、runner、测试和
本 milestone 说明会进入版本控制。

## 7. 自动测试

新增测试覆盖：

- Gold Set 正好 100 条、ID 唯一、75/25 固定拆分；
- 13 类有效知识和历史版本全部有正例；
- 无答案、hard negative、多意图、历史时间和安全场景存在；
- graded relevance 的 Recall、Precision、MRR 和 nDCG 计算；
- hard negative 与正确召回分开判定；
- 无答案必须返回空结果；
- 100 条数据通过真实 `KnowledgeSearchService` 执行；
- 报告包含 Embedding 和检索配置元数据。

## 8. 面试时如何讲

可以这样概括：

> 我没有直接换一个 Embedding 模型然后挑几个成功例子演示，而是先把检索从 Agent 链路中拆出来，
> 建立 100 条可审查 Gold Set。标签使用稳定的文档版本而不是 chunk 编号，支持分级相关性、
> hard negative、无答案、历史版本和跨租户验证。第一版真实 PostgreSQL 基线只有 91.4% Recall@3，
> 明确暴露了隐含意图、多意图和哈希碰撞问题。接下来所有 Embedding 和 reranker 都在同一个冻结
> 集合上 A/B，所以模型选择有数据依据，而不是凭感觉。

## 9. 下一步

下一阶段建议：

1. 抽象 `EmbeddingProvider`；
2. 保留确定性哈希实现给快速 CI；
3. 为 Docker Demo 接入真实中文/多语种 Embedding；
4. 在 `dev` 上校准 RRF 和相关性门槛；
5. 只在方案固定后运行 `holdout`；
6. 根据 hard negative 失败决定是否增加 reranker；
7. 对比 Keyword only、Hash hybrid、Real embedding hybrid、Real embedding + reranker。
