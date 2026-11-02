# Ch03 验收报告 · C 出口（退款）

> 日期：2026-11-03 · 模型：`deepseek-chat` · 检索：`dense` · 库：`data/app.db`（20 单）
> 对应 spec：[ch03-rules-exit-design.md](../superpowers/specs/ch03-rules-exit-design.md)
> 对应 plan：[ch03-rules-exit.md](../superpowers/plans/ch03-rules-exit.md)

## 结论

**通过，但困难组没有余量。**

- 简单组、金额、四类转人工路径、9 状态可达性全部达标；
- 困难组模型 **7/10**，刚好压线；模型原始分存在 ±1 抖动（H09）。
- 流水线最终结论只有 **5/10**：不是模型的问题，是规则表判错时把正确答案升级成了转人工。
  这部分要在 Ch04 之前修规则，不是换模型。

## 验收清单

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | `pytest` 全绿 | ✅ **586 passed, 41 skipped** | `uv run pytest -q` |
| 2 | 简单组 ≥ 90%，分歧率 < 10% | ✅ **20/20（100%）**，分歧 0/20 | `scripts/eval_refund.py` |
| 3 | 困难组 A ≥ 3/4 | ✅ **3/4** | 同上 |
| 4 | 困难组 B ≥ 4/6 | ✅ **4/6** | 同上 |
| 5 | 金额对拍 10/10 | ✅ **10/10** | 同上 |
| 6 | 9 状态可达 | ✅ | `test_all_nine_nodes_reachable_from_entry` |
| 7 | 17 条转移各有测试 | ✅ | `test_edge_table_matches_spec` + 各守卫单测 |
| 8 | 四类转人工各一条 | ✅ | `tests/graph/rules/test_transfer_paths.py`（6 条） |
| 9 | 端到端 P95 < 30s | ✅ **4.72s**（n=5） | 见下「延迟」 |
| 10 | `00-tasks.md` P3 勾选 | ✅ 13/13 | `docs/superpowers/plans/00-tasks.md` |

## 判定评测（`scripts/eval_refund.py`）

| 组 | 模型 | 规则 | 流水线 |
|---|---|---|---|
| 简单组（20） | **20/20** | 20/20 | 20/20 |
| 困难组 A（4） | 3/4 | 2/4 | 3/4 |
| 困难组 B（6） | 4/6 | 0/6 | 2/6 |
| **合计（10）** | **7/10** | **2/10** | **5/10** |

- 简单组：错放率 0%（0/9）、错拦率 0%（0/10）、该转却给结论 0
- 模型 vs 规则分歧：简单组 0/20；困难组 B 2/6（H06 / H10）
- 详细归因见 [refund-eval.md](./refund-eval.md) §三

### 验收用的是"模型"列，不是"流水线"列

spec 验收项 9 测的是 `judge`。流水线分数同时受规则质量影响：

- 模型判错 → `compliance` 拦成转人工（好）
- **规则判错 → 模型答对也被拦成转人工（坏）**

H06 / H10 就是后一种：模型判对、规则判错，流水线被扣 2 分。

## 四类转人工路径

| 场景 | 触发节点 | 测试 |
|---|---|---|
| 模型拿不准（confidence < 0.7） | `judge` | `test_low_confidence_transfers` |
| 模型与代码分歧 | `compliance` | `test_divergence_transfers` |
| 政策检索为空 | `query_data` | `test_empty_policy_transfers` |
| 日期倒挂 | `validate` | `test_date_anomaly_transfers` |
| 追问次数用尽（附） | `validate` | `test_retry_exhausted_transfers` |
| 正常路径不转人工（对照） | — | `test_happy_path_does_not_transfer` |

全部打桩（模型 / DB / 检索），CI 可跑，不依赖外部服务。

## 延迟

5 轮真实退款，全部 `route=rules`：

```
4.72s  订单1001 我不想要了
4.63s  订单1002 不想要了
4.62s  订单1003 不想要了
4.34s  订单1004 辅食机有异响，我试了一次，能退吗
4.36s  订单1002 我不想要了

n=5  min=4.34s  median=4.62s  max=4.72s
P95≈4.72s（样本 5，P95 退化取最大值）  预算 30s
```

> 单轮 4 次模型调用（分类 / 抽取 / 判定 / 回复）合计约 4.5s。
> 首次调用可能到 13s（连接建立 + 服务端冷启动），稳定态远低于预算。
>
> 注意「订单1004 辅食机有异响，我试了一次」不带「能退吗」时会被判成**投诉**（0.60），
> 走 `human` 出口——这是意图分类的正确行为，不是退款链路的问题。

## 已知限制（不阻塞本章）

| 限制 | 现状 | 计划 |
|---|---|---|
| 困难组压线 | 7/10，模型有 ±1 抖动（H09 在 ok / not_intact 间摇摆）；n=10 的 95% CI ≈ [40%, 89%] | **P7-8**：分层扩充到 n≥20 + 留盲测，再谈精确准确率 |
| 流水线覆盖 5/10 | H06 / H10 规则判错，正确答案被升级为转人工 | 调规则顺序、细化 `packaging_opened` |
| 数据模型缺口 | H05 无"客服承诺"字段；H08 `condition_ok` 是订单级 | 后续章节评估 |
| 跨轮重试计数 | `validate_retry` 不落库，"追问 2 次转人工"只在单轮内生效 | P5-3 |
| `interrupt` / park | 未实现，跨轮恢复靠 DB | P5-12 |

## 复现

```bash
uv run pytest -q
uv run python scripts/eval_refund.py
uv run python scripts/eval_refund.py --skip-model   # 只跑金额
uv run python scripts/try.py graph "订单1002 不想要了"
```
