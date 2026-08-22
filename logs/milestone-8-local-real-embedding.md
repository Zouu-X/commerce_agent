# Milestone 8：本地真实 Embedding Provider

## 1. 这个 milestone 解决什么问题

Milestone 3 为了零 Key、快速测试和确定性复现，使用了 64 维特征哈希向量。它证明了
`pgvector + keyword + RRF + citation` 的链路能跑通，但哈希特征并不是通过训练得到的语义空间，
不足以作为求职 Demo 最终的 RAG 证据。

本 milestone 的目标不是简单安装一个模型，而是回答四个可验证的问题：

1. 什么模型适合中文电商短文档、小型项目和普通 CPU；
2. 真实 Embedding 应该替换 LLM，还是作为独立模型和 LLM 配合；
3. 替换后是否真的优于原基线；
4. 模型、向量库、离线测试和重建流程如何避免互相污染。

最终方案是：运行时使用 `BAAI/bge-small-zh-v1.5`，通过 FastEmbed + ONNX Runtime 在 CPU
本地编码；Hash provider 降级为测试专用 test double；检索仍保留关键词召回、语义召回和加权
RRF，而不是改成纯向量检索。

## 2. Embedding 模型与 LLM 的分工

Embedding 模型和对话 LLM 是两个不同组件：

- Embedding 模型把 query 和 document 分别转换成向量，用于快速找候选证据；
- 对话 LLM 读取已经找出的少量证据，决定工具调用并组织最终回复；
- Embedding 输出不是自然语言，也不负责执行 Agent 决策；
- 同一个向量空间必须同时编码查询和文档，不能拿不同模型生成的向量直接比较。

因此本项目继续使用 DeepSeek 作为 Agent 的对话模型，同时新增一个独立、无需 API Key 的本地
Embedding 模型。这是主流 RAG 中“检索模型负责召回，生成模型负责回答”的职责拆分。

## 3. 候选模型调查

调查优先看官方模型卡、官方项目和许可，并按当前约束比较：中文检索质量、CPU 体积、向量维度、
上下文长度、许可、是否需要登录下载，以及是否有轻量 ONNX 运行方式。

| 候选 | 主要特征 | 结论 |
|---|---|---|
| `BAAI/bge-small-zh-v1.5` | 中文专用，24M，512 维，最长 512 token，MIT；BGE 官方 C-MTEB Retrieval 61.77 | 选用。当前政策切片少于 280 个汉字，容量足够，FastEmbed ONNX 约 0.09GB |
| `BAAI/bge-base-zh-v1.5` | 中文专用，768 维；官方 C-MTEB Retrieval 69.49 | 公开指标更强，但模型、索引和内存更大；先由 Gold Set 判断 small 是否足够 |
| `intfloat/multilingual-e5-small` | 约百种语言，384 维，query/passage 前缀；官方 BGE 表中中文 Retrieval 59.95 | 多语言是本项目不需要的成本，中文公开结果略低于 BGE-small-zh |
| `BAAI/bge-m3` | 多语言，1024 维，8192 token，同时支持 dense/sparse/ColBERT | 功能强但约 0.6B，对 28 个中文短切片明显过度配置 |
| `Qwen/Qwen3-Embedding-0.6B` | 0.6B，1024 维，32K，上下文长、支持 MRL | 质量和能力有吸引力，但本地 CPU 冷启动和内存不适合小 Demo |
| IBM Granite Embedding 97M Multilingual R2 | 97M，384 维，长上下文，Apache 2.0，2026 新模型 | 许可和体积不错，但长上下文、多语言和代码能力在当前语料上没有直接收益 |
| Google EmbeddingGemma 300M | 300M，多语言，MRL | 更重且下载存在访问/许可摩擦，不利于招聘方一条命令复现 |

主要资料：

- BGE-small-zh 官方模型卡：https://huggingface.co/BAAI/bge-small-zh-v1.5
- BGE 总模型卡与 C-MTEB 对比：https://huggingface.co/BAAI/bge-base-zh-v1.5
- BGE-M3：https://huggingface.co/BAAI/bge-m3
- multilingual-e5-small：https://huggingface.co/intfloat/multilingual-e5-small
- Qwen3-Embedding：https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- Granite Embedding：https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2
- EmbeddingGemma：https://huggingface.co/google/embeddinggemma-300m
- FastEmbed 官方项目：https://github.com/qdrant/fastembed

## 4. 为什么选择 FastEmbed，而不是直接安装 PyTorch

SentenceTransformers/PyTorch 当然可以运行 BGE，但本项目只有 28 个短切片，重点是可解释的 RAG
链路，不需要训练模型。FastEmbed 使用 ONNX Runtime，当前模型包约 90MB，CPU 即可运行，避免
为了一个小模型把数 GB 深度学习运行时装进镜像。

FastEmbed 还提供 `query_embed()` 和 `passage_embed()` 两条入口。BGE 是非对称检索模型：query
侧需要检索指令语义，document 侧不需要。由 provider 封装两条入口，可以避免调用方忘记前缀或
把两侧编码方式写反。

首次 Docker 启动会下载模型；模型目录挂载为 named volume，之后重建容器仍复用缓存。这个方案
没有把 90MB 二进制提交到 Git，也不需要招聘方申请 Embedding API Key。

## 5. 先实验，再决定检索结构

接入代码前，先用 `retrieval-gold-v1` 做了一次内存实验。文档使用 `passage_embed`，query 使用
`query_embed`，只在 75 条 dev 上选择阈值，再查看冻结的 25 条 holdout。

### 5.1 纯向量结果

dev 上选择绝对 cosine 阈值 0.60 后：

| split | 通过率 | Recall@3 | MRR | No-answer |
|---|---:|---:|---:|---:|
| dev | 57.33% | 81.43% | 82.14% | 100% |
| holdout | 60.00% | 86.96% | 84.78% | 100% |

纯向量会把“语义相近”当成“业务上可以引用”。例如质量退货、无理由退货、退款到账都涉及退货/
退款，但它们不是可以互换的政策。真实向量并没有让关键词和业务边界变得多余。

### 5.2 Hybrid 结果

保留原来的关键词召回、概念特征和加权 RRF，只在 dev 校准 BGE 的绝对向量阈值 0.65 与相对最佳
证据阈值 0.95：

| split | 通过率 | Recall@3 | MRR | nDCG@5 | No-answer | Hard-negative 命中 |
|---|---:|---:|---:|---:|---:|---:|
| dev（75） | 86.67% | 90.71% | 93.57% | 91.49% | 100% | 1.61% |
| holdout（25） | 92.00% | 93.48% | 95.65% | 93.97% | 100% | 0% |
| all（100） | 88.00% | 91.40% | 94.09% | 92.11% | 100% | 1.23% |

与 Milestone 7 的 Hash 基线比较：

| 配置 | 通过率 | Recall@3 | MRR | nDCG@5 | No-answer | Hard-negative 命中 |
|---|---:|---:|---:|---:|---:|---:|
| Hash + keyword + RRF | 83% | 91.40% | 94.09% | 92.11% | 85.71% | 6.17% |
| BGE + keyword + RRF | 88% | 91.40% | 94.09% | 92.11% | 100% | 1.23% |

结论需要诚实表达：排序类指标没有变化，因为在这个很小、关键词明确的政策库里，权重为 2 的关键词
通道仍主导相关文档排序。真实 Embedding 的可测收益主要是更好的证据拒绝：7 条 OOD 全部返回空，
相似但错误的 hard negative 明显减少。它证明了真实模型的价值，但也说明不能用“接了 Embedding”
代替数据集和指标。

## 6. Provider 设计

新增 `EmbeddingProvider` 协议，统一暴露：

- `provider_name`、`model_name`、`dimensions`；
- `embed_query(text)`；
- `embed_documents(texts)`。

实现有两种：

1. `FastEmbedEmbeddingProvider`：运行时默认，懒加载模型，支持批量 document 编码；
2. `DeterministicHashEmbeddingProvider`：只给单元测试和离线无模型环境使用。

Hash provider 仍在历史 64 个桶上计算，再补零到 512 维。这样数据库 schema 与真实模型一致，同时
Milestone 7 的确定性基线不因哈希碰撞模式改变而失去可比性。

`KnowledgeSearchService` 在一次请求中只计算一次 query embedding，并通过 `asyncio.to_thread`
把 CPU 推理移出 event loop；同一个向量同时用于候选排序和绝对相关性门禁，避免旧实现重复计算。

## 7. 向量生命周期与安全防护

数据库迁移把 `vector(64)` 升级为 nullable `vector(512)` 并重建 HNSW 索引。旧模型向量不能通过
补零或截断变成新模型向量，所以迁移明确丢弃旧 embedding 值，随后由独立 reindex 重建。

`python -m app.knowledge.reindex` 会：

1. 按 document + chunk 稳定顺序读取知识；
2. 使用 `title + content` 批量生成 passage embedding；
3. 写入 512 维向量；
4. 在 `metadata_json` 保存 provider、model 和 dimensions；
5. 已经匹配的切片跳过，`--force` 可强制重建。

API 的 Docker 启动顺序变为 migration → seed-if-empty → reindex-if-stale → uvicorn。检索服务还会
再次检查向量元数据；如果向量来自不同 provider/model，就不让它进入语义排序，只使用关键词通道。
这是为了避免最危险的一类静默错误：query 用新模型编码，数据库却仍是旧模型向量，两边维度即使
碰巧一样，cosine 分数也没有意义。

常用命令：

```bash
make up
make reindex
make eval-retrieval
```

配置项：

```dotenv
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_CACHE_DIR=/models/fastembed
EMBEDDING_THREADS=2
EMBEDDING_MIN_VECTOR_SIMILARITY=0.65
EMBEDDING_MIN_RELATIVE_RELEVANCE=0.95
```

## 8. 测试与验收

完成的验证：

- Ruff 通过；
- mypy strict 通过；
- 82 个后端 pytest 通过；
- Docker 冷启动成功下载并缓存约 90MB 模型；
- Alembic 在已有 Milestone 7 数据库上升级到 `vector(512)`；
- PostgreSQL 检查 28/28 个切片都有向量；
- 28/28 个切片元数据均为 `fastembed / BAAI/bge-small-zh-v1.5 / 512`；
- 真实 PostgreSQL + pgvector 运行 100 条 Gold Set，整体 88/100、holdout 23/25；
- provider 报告元数据不再硬编码为 Hash；
- reindex 的首次更新和第二次幂等跳过都有单元测试。

## 9. 仍然存在的限制

本次质量门禁仍未通过。12 条失败中，11 条是相关来源未进入 Top 3，主要集中在隐含/对比表达和
需要两份证据的 multi-intent；另有 1 条 hard negative、1 条内容要求缺失（同一 case 可有多个
失败原因）。这意味着下一步不应该继续盲调 Embedding 阈值，而应优先评估：

- query decomposition：把“取消条件 + 退款到账”拆成两个检索子问题；
- 轻量 cross-encoder reranker：对 Top-N 候选做更精确的 query-document 判断；
- 扩充真实语料后再检查 HNSW 执行计划、召回深度和过滤条件；
- 把新增长期数据保留为真正未见过的 holdout，防止持续调参污染评测。

模型没有固定到 Hugging Face commit SHA；FastEmbed 0.8 的模型映射和 Docker cache 能保证当前
演示可重复，但若要生产级供应链可复现，还应固定模型 artifact digest 并做启动校验。本项目按
AGENTS.md 定位不追求生产级实现，因此把这个差距明确记录，而不是引入更重的模型仓库管理。

## 10. 面试讲法

> 我最初用确定性 Hash 向量把 pgvector、混合检索、引用和安全范围跑通，但 Gold Set 证明它不是真正
> 的语义通道。接入前我比较了 BGE、E5、Qwen、Granite 和 EmbeddingGemma，最后选了 24M 的
> bge-small-zh-v1.5，用 FastEmbed/ONNX 在 CPU 本地运行。关键不是“换了一个模型”，而是先做纯
> 向量对照：holdout 只有 60%，因为语义相似不等于业务证据正确；保留关键词和 RRF 后 holdout
> 达到 92%。全量排序指标与 Hash 基线持平，但 no-answer 从 85.71% 提升到 100%，hard negative
> 从 6.17% 降到 1.23%。工程上我把 query/document 编码拆成 provider 接口，记录每个切片的模型
> 元数据，用可幂等 reindex 管理向量生命周期，并拒绝混用不同模型的向量。这个结果既展示真实
> Embedding，也用评测说明它在系统里具体改善了什么、还没有解决什么。
