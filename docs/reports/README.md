# reports 目录索引

> 这里放**运行记录与结论**，不放设计。设计在 `docs/design/`，spec / plan 在 `docs/superpowers/`。
> 命名不分类，靠这张表找文件——改路径会打断 40 多处引用，不划算。

## 一、改动记录（每章一份，只增不改）

| 文件 | 内容 |
|---|---|
| [00-change-log-overview.md](./00-change-log-overview.md) | 格式说明 + 章节索引。**新章先在这里登记** |
| [ch01-data-foundation-changes.md](./ch01-data-foundation-changes.md) | 数据地基：10 表、seed、越权 |
| [ch02-walking-skeleton-changes.md](./ch02-walking-skeleton-changes.md) | 主流程骨架：意图、路由、检索、知识出口 |
| [ch03-rules-exit-changes.md](./ch03-rules-exit-changes.md) | C 出口：退款 9 状态流水线、判定、评测 |
| [ch04-data-exit-changes.md](./ch04-data-exit-changes.md) | B 出口：订单 / 物流 / 库存查询 |

## 二、验收报告（一章做完时写）

| 文件 | 内容 |
|---|---|
| [ch02-acceptance.md](./ch02-acceptance.md) | 主流程骨架验收 |
| [ch03-acceptance.md](./ch03-acceptance.md) | 退款流水线验收：清单、指标、转人工路径、延迟、局限 |
| [ch04-acceptance.md](./ch04-acceptance.md) | 数据查询验收：三类查询、边界场景、延迟、零模型调用 |

## 三、评测报告（有数据、有结论）

| 文件 | 内容 | 什么时候看 |
|---|---|---|
| [retrieval-eval.md](./retrieval-eval.md) | dense / bm25 / hybrid 三策略对比，hit@3、MRR | 改检索策略前 |
| [gate-eval.md](./gate-eval.md) | 证据闸阈值标定（0.55） | 调 `evidence_threshold` 前 |
| [knowledge-eval.md](./knowledge-eval.md) | 知识出口的覆盖率与引用质量 | 改语料或 prompt 后 |
| [refund-eval.md](./refund-eval.md) | 退款判定：模型 / 规则 / 流水线三列，未命中归因 | 改 prompt 或规则表前后 |

**共同约定**：每份都要写清数据来源、样本量、结论的适用边界。
没有样本量的百分比不许写进简历。

## 四、环境与手工检查

| 文件 | 内容 |
|---|---|
| [local-env.md](./local-env.md) | Chroma（Docker）、Ollama（bge-m3）、端口、集合名 |
| [manual-checks.md](./manual-checks.md) | 不依赖自动化的手工验证步骤 |

## 五、术语与措辞

| 文件 | 内容 |
|---|---|
| [metrics-glossary.md](./metrics-glossary.md) | hit@3 / MRR / 错放率 / 错拦率等指标释义 |
| [resume-positioning.md](./resume-positioning.md) | 简历措辞与实现状态对照；每条标注"能不能写、会被追问什么" |

## 六、面试材料

| 文件 | 内容 |
|---|---|
| [../interview/README.md](../interview/README.md) | 面试材料索引 + 跨章主线 |
| [../interview/ch01-data-foundation.md](../interview/ch01-data-foundation.md) | Ch01：双写 DDL、幂等、越权、seed 覆盖度 |
| [../interview/ch02-retrieval-skeleton.md](../interview/ch02-retrieval-skeleton.md) | Ch02：查表路由、检索选型、证据闸、RAG 评测 |
| [../interview/ch03-refund.md](../interview/ch03-refund.md) | Ch03：模型判定 + 代码复算、评测设计、踩坑故事 |
| [../interview/database-qa.md](../interview/database-qa.md) | 数据库问答：索引 / 事务 / WAL / async ORM |
| [../interview/self-test.md](../interview/self-test.md) | 自测方法：抽题表、评分标准、模拟面试与演示脚本 |

## 新增报告时的检查清单

- [ ] 写清**数据来源**（哪份语料、哪组样本、跑了几次）
- [ ] 写清**样本量**和结论的**适用边界**（能不能推广）
- [ ] 给出**失败样本的归因**，不只报总分
- [ ] 在本文件对应的表里登记一行
