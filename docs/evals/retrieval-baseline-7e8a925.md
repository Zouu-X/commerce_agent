# Retrieval Gold Set 基线（`7e8a925`）

这是 `retrieval-gold-v1.2-human-review` 在源提交
`7e8a925b5902471828911b91acf2b8df15d88ea9`（`Merge Gold Set human review`）上的精简、可提交快照。
Gold Set 已由项目 owner 人工 review 并修改；本报告冻结 review 后的标签、runner 配置和结果，
文件名中的短 SHA 用于避免后续运行覆盖本基线。

> 适用范围：只评测 `KnowledgeSearchService` 的 Retrieval 与确定性 Query Decomposition，
> 不经过 Agent、工具路由或生成模型。因此生成模型、生成 Prompt、token 用量和 API 费用均不适用；
> 下文的 BGE 是 embedding model，不是回答生成模型。

## 运行身份与证据完整性

| 项目 | 冻结值 |
|---|---|
| Source commit | `7e8a925b5902471828911b91acf2b8df15d88ea9` |
| Run start (UTC / Asia/Shanghai) | `2026-08-23T03:55:48.792167Z` / `2026-08-23 11:55:48.792167+08:00` |
| Run complete (UTC / Asia/Shanghai) | `2026-08-23T03:55:49.661970Z` / `2026-08-23 11:55:49.661970+08:00` |
| Report schema | `1.0` |
| Dataset | `commerce-rag-retrieval` / `retrieval-gold-v1.2-human-review` |
| Dataset path | `backend/app/evaluations/data/retrieval_gold_v1.jsonl` |
| Dataset split | all：75 dev + 25 holdout = 100 cases |
| Dataset SHA-256 | `badb968a78edbe8d7645c1afce49caa185a01aaa6445fa54c6b11c32663cfb8d` |
| Runner SHA-256 | `2f2c809cb6ff931f1e8aa980a92ca6e713a1800a9c754ac189fcada0ae955d9b` |
| Raw local report SHA-256 | `10ffdb7a82e74f151458a0b760330f2808a597dfda8f657a8779149f52d8ea25` |

原始 JSON 没有记录 Git SHA，且运行完成时间早于 review feature commit `26fd861` 和 merge commit
`7e8a925`。本基线通过以下事实把结果归档到 `7e8a925`：原始报告声明数据集版本
`retrieval-gold-v1.2-human-review`；报告内容反映 review 后的标签；数据集与 runner 在 `26fd861`
和 `7e8a925` 中的 SHA-256 均分别等于上表值。这个校验能证明所归档的版本化输入与代码内容，
但不能补写原始运行当时未采集的 Git 元数据。

## 模型与检索配置

| 项目 | 值 |
|---|---|
| Strategy / config | `hybrid_rrf` / `hybrid-rrf-v3-query-decomposition` |
| Embedding provider | `fastembed` |
| Embedding model | `BAAI/bge-small-zh-v1.5` |
| Dimensions | 512 |
| RRF | `k=60`，keyword weight `2.0`，vector weight `1.0` |
| Query decomposition | `deterministic_commerce_intent_planner`，最多 3 个 subqueries |
| Merge | `round_robin_with_per_subquery_top_1_guarantee` |
| Retrieval limit | top 5（case 通过条件检查 relevant source 是否进入 top 3 且排第 1） |

检索阈值：

- `min_keyword_relevance = 0.12`
- `min_vector_similarity = 0.65`
- `min_vector_keyword_support = 0.05`
- `min_relative_relevance = 0.95`

## 结果

| 指标 | 全量（100） | Dev（75） | Holdout（25） |
|---|---:|---:|---:|
| Cases passed / pass rate | 96 / **96.00%** | 72 / **96.00%** | 24 / **96.00%** |
| Answerable / no-answer / hard-negative | 93 / 7 / 80 | 70 / 5 / 61 | 23 / 2 / 19 |
| Recall@1 | 91.40% | 90.71% | 93.48% |
| Recall@3 | **95.70%** | **95.71%** | **95.65%** |
| Recall@5 | 95.70% | 95.71% | 95.65% |
| Precision@3 | 95.70% | 95.71% | 95.65% |
| MRR | **95.70%** | **95.71%** | **95.65%** |
| nDCG@5 | **95.21%** | **95.06%** | **95.65%** |
| No-answer accuracy / false-positive rate | 100% / 0% | 100% / 0% | 100% / 0% |
| Hard-negative case hit rate | **0%** | 0% | 0% |
| Forbidden source / scope violation rate | 0% / 0% | 0% / 0% | 0% / 0% |
| Multi-intent decomposition rate | 100% | 100% | 100% |
| Decomposition intent Precision / Recall | 100% / 100% | 100% / 100% | 100% / 100% |
| Non-multi-intent decomposition rate | 0% | 0% | 0% |
| Subquery resolution rate | 100% | 100% | 100% |
| Content requirement failure rate | 0% | 0% | 0% |
| P95 latency | 12 ms | 12 ms | 7 ms |

延迟是单次本地运行的诊断值，不应当作跨机器性能承诺。

## 质量门

全量、dev 和 holdout 的 `quality_gate_passed` 都为 `true`。各范围均通过以下门槛：

- Recall@3 ≥ 95%，MRR ≥ 90%，nDCG@5 ≥ 95%；
- no-answer false-positive rate ≤ 5%；
- forbidden source hit rate = 0，scope violation rate = 0；
- non-multi-intent decomposition rate = 0；
- decomposition intent Precision = 100%，Recall = 100%；
- subquery resolution rate ≥ 90%。

注意：质量门通过不等于所有 case 通过。质量门是聚合阈值，本次仍有 4 个 case 失败。

## 失败分类与 case

失败类型共两类，各出现 4 次，并在同一组 case 上同时出现：

- `missing_relevant_top_3`：4
- `relevant_source_not_ranked_first`：4

| Case | Split | 实际 source | 缺失的 relevant source |
|---|---|---|---|
| `return_003` | dev | 无结果 | `no-reason-return:v1` |
| `return_004` | holdout | `refund-timing:v1` | `no-reason-return:v1` |
| `stale_004` | dev | 无结果 | `logistics-stale:v1` |
| `compensation_005` | dev | `price-protection:v1` | `delay-compensation:v1` |

未出现 hard-negative 命中、forbidden source 命中、scope 泄漏、no-answer 误召回、内容要求缺失或
decomposition 意图错误。剩余失败均属于单意图查询的漏召回或相邻政策排序问题。

## README 数字核对

在源提交 `7e8a925` 的 README 中，最终配置展示为：通过率 96%、Recall@3 95.70%、MRR
95.70%、nDCG@5 95.21%、No-answer 100%、Hard-negative 命中 0%；还声明 holdout 通过率
96%、7 条复合问题全部正确拆分、剩余 4 条失败。以上数字与原始 JSON 一致，未发现不一致。

README 的 A/B 表还包含 Hash test double 和“BGE-small-zh + 关键词 + RRF”两组历史结果；
它们不在本次 `retrieval-latest.json` 中，因此本报告不将其重新声明为已由本次产物核验。

## 复现

在项目根目录运行：

```bash
make eval-retrieval RETRIEVAL_SPLIT=all
```

该目标会构建 API 镜像、升级数据库、重置确定性 Demo 数据、重建 embedding，并运行 100 条
Retrieval Gold Set。需要 Docker；首次运行还需获取约 90 MB 的 BGE ONNX 模型。输出为：

- `eval-results/retrieval-evaluation-<timestamp>.json`
- `eval-results/retrieval-latest.json`

逐项比较时应同时确认 source commit、dataset/config version、embedding provider/model/dimensions、
阈值和 split，不能只比较通过率。

## 原始报告策略

172 KB 的 `eval-results/retrieval-latest.json` 是本地逐 case 诊断产物，受 `.gitignore` 中
`eval-results/*.json` 规则排除，不随仓库提交，也可能被下一次运行覆盖。本文件只保留招聘审查和
回归比较需要的配置、聚合指标、失败证据与 SHA-256；需要完整命中分数和逐 case 证据时，应在相同
提交上用上述命令重跑并核对摘要。
