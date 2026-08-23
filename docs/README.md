# 技术文档索引

README 负责在一分钟内说明项目价值；这里保存实现、复现和边界细节。

| 文档 | 内容 |
|---|---|
| [Architecture](./architecture.md) | 端到端执行顺序、模块映射、RAG、审批与 Trace 设计 |
| [Evaluation](./evaluation.md) | Retrieval / Mock / DeepSeek 三类评测的职责、指标和复现 |
| [Security boundaries](./security-boundaries.md) | 已实现控制、可信假设、已知风险与公开部署前清单 |
| [Demo guide](./demo-guide.md) | 本地启动、三分钟演示、curl 与开发命令 |
| [Retrieval baseline](./evals/retrieval-baseline-7e8a925.md) | owner-reviewed Gold Set 的冻结报告 |
| [DeepSeek 3-run baseline](./evals/deepseek-3run-baseline-7e8a925-20260823.md) | 三次真实模型运行、波动、费用和失败分类 |
| [v0.1.0 release notes](./release-notes-v0.1.0.md) | 首个完整 Demo 版本的能力、证据与限制 |

`project_plan.md` 是历史规划记录，可能包含早期设想。描述当前实现时，以代码、上述技术文档和绑定
source commit 的评测报告为准。
