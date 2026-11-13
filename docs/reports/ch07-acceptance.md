# Ch07 验收报告 · 评测

> 日期：2026-11-09 ｜ 语料 `kb_version=80bd26573951`（57 chunks）
> 数据集：`ecom-eval-v1`（44 条）｜ 被测系统：ecom-agent `:8081`
> 模型 / judge：`deepseek-chat` ｜ 检索：`dense` ｜ judge prompt `v2`
> 确定性基线：[run-local-06.md](./run-local-06.md) **44/44**
> 质量快照：[run-03.md](./run-03.md)（EvalFlow run #35）

## 结论

**通过，可以进入 Ch08。** 44 条端到端评测、确定性检查、阈值曲线、
质量传导、退款困难组全部有数据；全量测试通过。

需要带进 Ch08 的两个已知边界：

1. **退款流水线低于模型**：模型 dev 10/10、held-out 15/15，流水线 8/10、13/15。
   差额全部是“模型判对、规则按整单字段否决，被 compliance 转人工”，
   不是错误退款。根因是 `condition_ok` / `packaging_opened` 只有订单级。
2. **held-out test 不是严格盲测**：运行前已定稿，但运行后修正过 T01/T03 的
   gold 冲突（与 B07 / 规则表对齐），且修改前已看过一次结果。后续再改 prompt
   必须换新样本。

## 验收清单

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 44 条评测集校验通过 | ✅ | `pytest tests/eval/test_eval_v1.py`（桶计数 10/8/8/6/4/4/4、唯一、语法、盲测比例） |
| 2 | `/api/eval/chat` 正常 / 关闭 / 错误映射 | ✅ | `tests/api/test_eval_api.py`：正常映射、关闭 404、422/504/404/429/500 |
| 3 | EvalFlow 导入并跑完 run-01 | ✅ | run #31；[run-01.md](./run-01.md) |
| 4 | run-02 指标变化可解释 | ✅ | run #32；[run-02.md](./run-02.md)；relevance 噪声用 [judge-variance.md](./judge-variance.md) 量化 |
| 5 | 阈值曲线 | ✅ | [gate-thresholds.md](./gate-thresholds.md)；0.53–0.56 双零，维持 0.55 |
| 6 | 质量传导 2×2 | ✅ | [quality-propagation.md](./quality-propagation.md)；四格有样本，人工核验 4 条，0 编造 |
| 7 | 退款困难组 n ≥ 20 + CI | ✅ | dev 10 + held-out 15；[refund-hard.md](./refund-hard.md)；Wilson CI + per-class |
| 8 | `/api/chat` 回归不变 | ✅ | `tests/api/test_chat_sse.py` + 确定性 44/44（run-local-06） |
| 9 | 全量测试通过 | ✅ | **1030 passed, 16 skipped**；`ruff check .` 通过 |

## 关键指标

### 端到端（run-03）

| 指标 | 值 |
|---|---|
| recall / hit@k / MRR | 1.000 / 1.000 / 1.000 |
| context_precision / recall | 0.875 / 1.000 |
| faithfulness | 1.000 |
| answer_relevance | 0.8909 |
| answer_correctness | 0.9977 |
| 确定性通过 | **44/44** |
| 成本 | $0.0923 / 44 条 |

### 证据闸

- 机械闸无法双零：0.53–0.56 放行 42/42，但漏「能分期付款吗」，误放 9.1%；
- 语义闸把双零区间扩到 **0.53–0.56**；
- 当前 **0.55** 位于平台中部，两侧余量约 0.02。

### 质量传导

- 自然 top3：42/42 命中，40 条计分样本全部“命中且完整”；
- 严格 top1：3 条未命中，人工核验 2 条有依据、1 条是正则误报；
- 截断 gold：1 条缺要点，回答明确说“资料里没有提到”；
- 错误证据 10 条：**0 编造**。

### 退款判定

| 数据集 | 模型 | 95% CI | 规则 | 流水线 |
|---|---|---|---|---|
| 简单组（20） | 19/20 | 76–99% | 20/20 | 19/20 |
| dev（10） | **10/10** | 72–100% | 3/10 | 8/10 |
| held-out（15） | **15/15** | 80–100% | 6/15 | 13/15 |

- held-out 中规则顺序组是参照组（3/3，gold 与规则表一致），其余 4 组 12/12；
- 流水线未命中的 T04/T05 是字段粒度问题，模型本身判对；
- 金额合同 10/10。

## 本轮修掉的两个问题

1. **评测脚本用错时间锚点**：`run_judge` 没把 case 的 `now` 传给 judge，
   固定日期的 gold set 会随运行当天漂移。修正后 Ch03 的 7/10 不可复现，
   当前 dev 为 10/10（prompt 修复后）。
2. **gold 冲突**：B07 / seed 1108 规定“已发货未签收的定作商品 →
   `reject_then_refund`”，H06/T01 原标 `not_applicable`。以规则表为准修正
   H06/T01/T03，并恢复 prompt 的状态优先级，B07 回归消除。

## 已知边界

| 边界 | 影响 | 下一步 |
|---|---|---|
| 退款字段粒度 | 流水线 8/10、13/15，差额是保守转人工 | 把 `condition_ok` / `packaging_opened` 拆到件级；或让 compliance 按件判断 |
| held-out 非严格盲测 | 15/15 不能当独立盲测准确率 | 下次 prompt 变更换新样本 |
| relevance 有 judge 方差 | 单次 run 的 ±2–4pp 不能当真实变化 | 关键结论看确定性指标；必要时 3 次取均值 |
| B5–B7 relevance 低 | 转人工/拒答被通用 judge 判成不相关 | 不按普通问答解读；用路由/动作确定性检查 |
| 简单组 G04 | 6 天边界被模型判成超期，19/20 | 低优先级；属于模型边界计算细节 |

## 复现命令

```bash
# 评测实例
ENABLE_EVAL_API=true \
DATABASE_URL=sqlite+aiosqlite:///./data/eval.db \
uv run uvicorn app.main:app --port 8081

# 确定性回归（44 条）
uv run python scripts/eval_e2e.py \
  --base-url http://127.0.0.1:8081 --out docs/reports/run-local-06.md

# 阈值 / 知识 / 质量传导
uv run python scripts/eval_gate.py --out docs/reports/gate-thresholds.md
uv run python scripts/eval_knowledge.py --out /tmp/knowledge.md

# 退款
uv run python scripts/eval_refund.py --out docs/reports/refund-hard.md

# 全量
uv run pytest -q
```

## 文档状态

- P7-1 … P7-8 全部 ✅；
- 改动记录索引 Ch07 → ✅；
- 报告索引见 [README.md](./README.md)。
