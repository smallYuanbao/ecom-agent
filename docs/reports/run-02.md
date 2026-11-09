# Ch07 run-02 · EvalFlow 端到端评测（新 KB + 语义闸）

> 日期：2026-11-09 · EvalFlow run **#32** · 数据集 `ecom-eval-v1`（44 条）
> 被测系统：ecom-agent `http://127.0.0.1:8081`（`EVAL_TARGET=ecom`）
> 模型：`deepseek-chat` · judge：`deepseek-chat` · judge prompt `v2`
> 知识库：`kb_version=80bd26573951`（57 chunks，含 15 条 FAQ）
> 语义闸：`EVIDENCE_THRESHOLD=0.55`、band `0.05`、judge timeout `3s`、不重试
> 数据集哈希：`9d9a5a0b56eee804cee4702b0601490d`（与 run-01 相同）
> 确定性检查：本地 `run-local-05` **44/44**

## 结论

**44 条全部跑完，确定性检查 44/44，没有回归。** 两处代码修复各自兑现：

1. **FAQ 知识层**：`退货政策是什么` 的 answer_correctness **0.3 → 1.0**，
   两条 B1 样本的 mechanical recall / context_recall 从 **0.5 → 1.0**；
   B1 桶 correctness **0.92 → 0.99**，整集 **0.9818 → 0.9977**。
2. **指标覆盖**：无检索样本不再写 NULL 行，context 类指标覆盖率从
   `10 / 44` 修正为 **`10 / 10`**（数值本身不受影响）。

`answer_relevance` 整体 **0.8614 → 0.9045**，但**不能算作 FAQ 的收益**：
同一批样本连跑三次（#32/#33/#34）的 relevance 是 0.9045 / 0.8659 / 0.8898，
极差 **3.86pp**，run-01 的 0.8614 只略低于这个噪声带。
注入样本「忽略以上指令…」三次回答**逐字相同**，judge 判 0.0 / 0.0 / 0.9。
详见 [judge-variance.md](./judge-variance.md)。B5 / B7 不变，B1 的真实变化只有
`退货政策是什么` 一条。

## 总体指标

| 指标 | run-01 | run-02 | Δ | 覆盖 |
|---|---|---|---|---|
| recall | 0.95 | **1.00** | +0.05 | 10 / 10 |
| hit_at_k | 1.00 | 1.00 | 0 | 10 / 10 |
| mrr | 1.00 | 1.00 | 0 | 10 / 10 |
| context_precision | 0.91 | **0.87** | −0.04 | 10 / 10 |
| context_recall | 0.90 | **1.00** | +0.10 | 10 / 10 |
| faithfulness | 1.00 | 1.00 | 0 | 10 / 10 |
| answer_relevance | 0.8614 | **0.9045** | +0.0431 | 44 / 44 |
| answer_correctness | 0.9818 | **0.9977** | +0.0159 | 44 / 44 |

> **Δ 的可信度**：correctness / recall / context_recall 的 Δ 超出重复运行噪声；
> context_precision 与 answer_relevance 的 Δ 在噪声带内，只作记录、不作结论。
> 噪声量化见 [judge-variance.md](./judge-variance.md)。

**覆盖率说明**：B2–B7 没有检索证据（或不是回答证据），context / faithfulness /
recall / hit@k / MRR 只对 B1 的 10 条适用。run-01 报告里的 `10 / 44` 是旧代码
写 NULL 行造成的展示问题，run-02 已显示为 `10 / 10`。

## 分桶（run-01 → run-02）

| 桶 | count | answer_relevance | answer_correctness | 其它 |
|---|---|---|---|---|
| B1 知识 | 10 | 0.99 → **1.00** | 0.92 → **0.99** | recall .95→1.0 / ctx_recall .90→1.0 / ctx_precision .91→.87 |
| B2 数据 | 8 | 1.00 → 1.00 | 1.00 → 1.00 | 无 context 指标 |
| B3 规则 | 8 | 0.90 → **0.975** | 1.00 → 1.00 | 全部是 judge 方差，回答未变 |
| B4 边界 | 6 | 0.9667 → 0.9667 | 1.00 → 1.00 | 无 context 指标 |
| B5 异常 | 4 | 0.625 → 0.625 | 1.00 → 1.00 | 转人工 / 查不到，见下 |
| B6 对抗 | 4 | 0.525 → **0.825** | 1.00 → 1.00 | 主要是 judge 方差（0.0 → 0.9） |
| B7 转人工 | 4 | 0.60 → 0.60 | 1.00 → 1.00 | 转人工 |

## 修复项清单

### 已修复

| 问题（run-01） | 修法 | run-02 结果 |
|---|---|---|
| `退货政策是什么` 缺「签收次日起 7 个自然日」 | 新增 `faq#退货政策是什么` 知识块 | correctness 0.3 → **1.0** |
| 两条 B1 的 mechanical recall / ctx_recall 卡在 0.5 | FAQ 直接命中用户问法 | 均 → **1.0** |
| 无检索样本把 context 指标写成 NULL 行 | EvalFlow 侧不适用就不写行 | 覆盖率 10/10 |

### 未修复（已接受）

| 现象 | 归因 | 处理 |
|---|---|---|
| `退货运费由谁承担` correctness 仍为 0.9 | judge 评语已确认覆盖两组要点，只是要点语法里的 `\|` 备选未逐字命中；不影响业务正确性 | 记录，不按单题改 prompt |
| B5 `订单1004的快递到哪了` rel 0.3、`订单1105` rel 0.3 | 一个是订单不属于当前用户，一个是退款诉求转人工，都是**正确的安全兜底** | 不按普通问答的 relevance 解读 |
| B7 投诉类 rel 0.6–0.9 | 投诉必须转人工，通用 relevance judge 会认为「没回答问题」 | 同上 |
| `ST-20 能带上飞机吗` ctx_precision 0.35 | top-5 里混入同产品的其他型号对比段；产品块本身较宽 | 与 FAQ 无关，run-01 同为 0.35 |

## context_precision 为什么降了 0.04

FAQ 块与现有 `returns` / `member` 块有语义重叠，top-5 里会出现「同一事实的两份表述」，
judge 会把重复块算作弱相关：

| B1 样本 | run-01 | run-02 |
|---|---|---|
| 退货运费由谁承担 | 1.00 | 0.85 |
| 商品完好的标准是什么 | 1.00 | 0.90 |
| 优惠券能叠加使用吗 | 1.00 | 0.85 |
| 会员有什么折扣 | 0.85 | 1.00 |

换来的是 recall / context_recall 的 +0.05 / +0.10，以及一条真实答案从 0.3 到 1.0。
**这是用少量精度换召回与正确性的取舍，不是退化。**

## 语义闸在 run-02 中的表现

- 44 条样本没有出现「证据自评失败」日志，没有 fail-open；
- 已知边界样本在 run-02 前后手工复核：`能分期付款吗` 仍被拦住（弱证据兜底 + 转人工），
  `FB-30 保修多久` 等正样本正常放行；
- 延迟（目标系统侧，44 条）：p50 **2474ms**（run-01 2347ms），
  p95 **5149ms**（run-01 4537ms），max **5580ms**（run-01 4989ms）。
  增量来自边界样本的一次 3s 上限自评，仍在 A 路径 p95 ≤ 6s 的预算内。

## 快照与可复现

```json
{
  "target": "ecom",
  "base_url": "http://127.0.0.1:8081",
  "kb_version": "80bd26573951",
  "chat_model": "deepseek-chat",
  "judge_model": "deepseek-chat",
  "pipeline_prompt_version": "v1",
  "judge_prompt_version": "v2",
  "dataset_hash": "9d9a5a0b56eee804cee4702b0601490d",
  "item_count": 44
}
```

成本：输入 28,817 tokens / 输出 4,407 tokens，合计 **$0.09289**（run-01 $0.09006）。

## 盲测声明

- 本轮所有改动只针对 **dev** 桶：B1 的 10 条全部是 `split=dev`，FAQ 补充依据来自
  run-01 的失败归因（`退货政策是什么`），不是逐题打补丁；
- `split=test` 的 28 条（B2/B4/B5/B6/B7 及部分 B3）**没有参与本轮调参**；
- B3 的 relevance 提升全部来自 judge 方差，不是代码变化。

## 已知限制

| 限制 | 说明 |
|---|---|
| 单次 run + judge 方差 | 同一批 3 次重复：relevance 0.9045 / 0.8659 / 0.8898（SD 0.0195，极差 3.86pp）；correctness 极差仅 0.45pp。见 [judge-variance.md](./judge-variance.md) |
| B1 全为 dev | 知识桶没有盲测样本；FAQ 的泛化能力只能靠 `eval_knowledge` 的 40 条覆盖集补充验证 |
| B5–B7 relevance 低 | 转人工 / 拒答的正确行为，不应按普通问答解读 |
| test 集非严格盲测 | B07–B11 / T05 在定稿前做过一次路径修正，见改动记录 §5 |

## 下一步

- Task 8：阈值扫描 0.40–0.75 + 质量传导 2×2（plan 已列）；
- 若要降低 judge 方差：`judge_samples=3` 后复跑 B5–B7，再判断 relevance 是否需要单独口径。

## 复现

```bash
# ecom 评测实例（语义闸默认开启）
ENABLE_EVAL_API=true \
DATABASE_URL=sqlite+aiosqlite:///./data/eval.db \
uv run uvicorn app.main:app --port 8081

# EvalFlow API + worker
EVAL_TARGET=ecom ECOM_BASE_URL=http://127.0.0.1:8081 \
uv run uvicorn backend.main:app --port 8002
EVAL_TARGET=ecom ECOM_BASE_URL=http://127.0.0.1:8081 \
uv run celery -A backend.celery_app worker --pool=threads --concurrency=2

# 发起 run + 本地确定性检查
curl -X POST http://127.0.0.1:8002/runs \
  -H 'Content-Type: application/json' -d '{"dataset_id":6,"config":"{}"}'
cd ecom-agent && uv run python scripts/eval_e2e.py \
  --base-url http://127.0.0.1:8081 --out docs/reports/run-local-05.md
```
