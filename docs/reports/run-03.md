# Ch07 run-03 · EvalFlow 质量快照（当前 prompt）

> 日期：2026-11-09 · EvalFlow run **#35** · 数据集 `ecom-eval-v1`（44 条）
> 被测系统：ecom-agent `http://127.0.0.1:8081`
> 知识库：`kb_version=80bd26573951` · 检索：**dense**（快照已修正）
> 模型 / judge：`deepseek-chat` · judge prompt `v2`
> 数据集哈希：`9d9a5a0b56eee804cee4702b0601490d`
> 确定性检查：[run-local-06.md](./run-local-06.md) **44/44**

## 结论

**验收基线：44 条全部完成，确定性检查 44/44，质量指标与 run-02 在噪声内一致。**

本轮是 Task 9 的 judge prompt 修复（C 出口）之后的当前快照。除了 B3/B4 的
relevance 有轻微波动，其余指标未变；correctness 仍为 0.9977。

## 总体指标（run-02 → run-03）

| 指标 | run-02 | run-03 | Δ |
|---|---|---|---|
| recall | 1.000 | 1.000 | 0 |
| hit_at_k | 1.000 | 1.000 | 0 |
| mrr | 1.000 | 1.000 | 0 |
| context_precision | 0.870 | 0.875 | +0.005 |
| context_recall | 1.000 | 1.000 | 0 |
| faithfulness | 1.000 | 1.000 | 0 |
| answer_relevance | 0.9045 | 0.8909 | −0.0136 |
| answer_correctness | 0.9977 | 0.9977 | 0 |

> relevance 的 −0.0136 落在 [judge-variance.md](./judge-variance.md) 测得的
> 同批极差 0.0386 之内，不能归因于 prompt 修复。

## 分桶

| 桶 | count | answer_relevance | answer_correctness | 其它 |
|---|---|---|---|---|
| B1 知识 | 10 | 1.000 | 0.990 | ctx_precision .875 / ctx_recall 1.0 / faith 1.0 |
| B2 数据 | 8 | 1.000 | 1.000 | — |
| B3 规则 | 8 | 0.950 | 1.000 | — |
| B4 边界 | 6 | 0.950 | 1.000 | — |
| B5 异常 | 4 | 0.625 | 1.000 | 转人工 / 查不到 |
| B6 对抗 | 4 | 0.750 | 1.000 | 拒答 |
| B7 转人工 | 4 | 0.600 | 1.000 | 转人工 |

B5–B7 的 relevance 仍低，是“安全兜底被通用 relevance judge 判成不相关”，
与 run-01/run-02 的已知边界一致。

## 快照修正验证

run-02 的 `config.retrieval` 误记为 `hybrid`；本轮：

```json
{"retrieval": "dense", "external": {"meta": {"retrieval_strategy": "dense"}}}
```

EvalFlow 现在以被测系统 `/api/meta` 为准，复现字段不再失真。

## 成本

输入 28,635 tokens / 输出 4,379 tokens，合计 **$0.09230**（run-02 $0.09289）。

## 已知限制

- 单次 run；judge 有方差，relevance 只看趋势；
- 本轮 judge prompt 修复只针对 C 出口，B1/B2 不受影响；
- B5–B7 的低 relevance 不应按普通问答解读。
