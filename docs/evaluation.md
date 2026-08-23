# 评测体系与证据口径

项目把检索、确定性工程回归和真实模型行为分开评测，避免用 Mock 高分替代真实模型质量，也避免把
生成失败错误归因给 Retrieval。

## 三层评测各自回答什么

| 层 | 路径 | 回答的问题 | 不证明什么 |
|---|---|---|---|
| Retrieval Gold Set | `KnowledgeSearchService -> PostgreSQL/pgvector` | scope、召回、排序、无答案回退、hard negative、复合意图拆分是否正确 | 不经过工具路由和生成模型，不证明最终回答质量 |
| Mock 端到端回归 | `AgentRuntime -> ToolRegistry -> Service -> Database` | Runtime、工具 Schema、服务、持久化、Trace 和 evaluator 是否稳定回归 | Mock 是规则路由器，不证明真实模型泛化、语言质量、延迟或费用 |
| DeepSeek 端到端 | 与 Demo 相同的真实 Provider 路径 | 工具选择、参数、任务完成、引用、客户输出、延迟、token、费用和非确定性 | 三次本地运行不是生产 SLO，也不能冻结远端 model alias 权重 |

## 冻结基线

### Retrieval Gold Set

[完整精简报告](./evals/retrieval-baseline-7e8a925.md) 绑定 source commit
`7e8a925b5902471828911b91acf2b8df15d88ea9`、数据集 SHA-256、runner SHA-256、Embedding 与检索
配置。`retrieval-gold-v1.2-human-review` 已由项目 owner review 并修改，共 75 dev + 25 holdout。

| 指标 | All | Dev | Holdout |
|---|---:|---:|---:|
| Cases passed | **96 / 100** | 72 / 75 | 24 / 25 |
| Recall@3 | **95.70%** | 95.71% | 95.65% |
| MRR | **95.70%** | 95.71% | 95.65% |
| nDCG@5 | **95.21%** | 95.06% | 95.65% |
| No-answer false-positive rate | **0%** | 0% | 0% |
| Hard-negative / forbidden / scope hit rate | **0% / 0% / 0%** | 0% / 0% / 0% | 0% / 0% / 0% |

全量、dev 和 holdout 的聚合质量门均通过，但不等于 100 个 case 全通过。4 个失败集中在单意图
查询的漏召回或相邻政策排序；没有 scope 泄漏、forbidden source、hard negative、no-answer 或
decomposition 失败。

### 真实 DeepSeek 三次端到端

[完整三次报告](./evals/deepseek-3run-baseline-7e8a925-20260823.md) 固定 source commit、backend tree、
Prompt `commerce-support-deepseek-v8`、60-case 数据集 `commerce-agent-core@milestone-6-v1`、Embedding
和成本单价。三次均完整执行 60/60 case，但三次质量门都未通过。

| Run | 通过 | 成本 | P95 |
|---|---:|---:|---:|
| 1 | 29/60（48.33%） | $0.04158028 | 4,527 ms |
| 2 | 23/60（38.33%） | $0.04160856 | 4,947 ms |
| 3 | 29/60（48.33%） | $0.04287542 | 5,017 ms |
| **合计 / 范围** | **81/180，pooled 45.00%** | **$0.12606426** | 4,527–5,017 ms |

单次 pass rate 均值为 `45.00% ± 4.71` 个百分点（总体标准差）。19 个 case 在三次运行中出现
pass/fail 变化，说明单次真实模型结果不足以代表质量。

稳定问题包括：

- Tool selection 平均 60.00%，低于 90% 目标；
- Necessary tool recall 平均 80.56%，低于 90% 目标；
- Task completion 平均 57.78%，低于 85% 目标；
- Citation coverage / correctness 为 91.67% / 75.00%，均未达 100% 目标；
- 24/24 action executions 失败，既包含模型未调用必要 `request_*`、额外前置查询和措辞不符，也
  反映 evaluator 对工具列表完全相同且有序的严格口径。

三次稳定通过 execution success、tool parameter validity、customer presentation、cross-scope
leakage 和 P95 latency 目标。报告中的 `unapproved_write_execution_rate` 实际由 write-action case
的 safety 失败率派生，非零不代表数据库真的执行了未审批写入；不能把该字段按名称直接解释。

## 复现命令

```bash
make eval-retrieval                     # all：100 cases
make eval-retrieval RETRIEVAL_SPLIT=dev
make eval-retrieval RETRIEVAL_SPLIT=holdout
make eval-mock                          # 60 cases，确定性、零模型费用
make eval                               # 60 cases，真实 DeepSeek、产生 API 用量
```

`make eval` 每次是一轮 60-case 运行。若要比较真实模型波动，应在相同 source commit、Prompt、数据集、
Embedding、成本配置和 seed 下独立运行至少三次，不要只反复读取 `latest.json`。

## 产物策略

逐 case 原始报告写入：

- `eval-results/evaluation-<run_id>.json`
- `eval-results/latest.json`
- `eval-results/retrieval-evaluation-<timestamp>.json`
- `eval-results/retrieval-latest.json`

这些文件大、包含本地 trace ID，且 `latest` 会被后续运行覆盖，所以由 `.gitignore` 排除。
`docs/evals/` 只提交精简、不可变的基线，必须记录 source commit、模型/Prompt、数据集版本与 hash、
运行配置、聚合结果和失败分类；如需逐 case 证据，应在相同提交上重跑并核对摘要。

## 费用与延迟解释

DeepSeek token 使用量来自 Provider usage，成本由配置的每百万 input/output token 单价估算，不是
供应商账单。远端 `deepseek-v4-flash` alias 的实际权重可能变化，Git commit 只能冻结客户端请求
配置。延迟来自一次本地 Docker 环境，不应当作跨机器或生产 SLA。
