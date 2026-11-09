# 检索策略对比

> 生成时间：2026-11-09 17:03 ｜ 语料 kb_version=`80bd26573951` ｜ 查询集 `retrieval_hard_v1.yaml`
> 规模：25 道基础题 / 35 条查询 ｜ 策略 3 ｜ 取回 5 条 / 评估前 3 条

> 评测集：`data/eval/retrieval_hard_v1.yaml`（25 道基础题展开的查询，拒答题不计入检索指标）
> 指标：hit@3，取回 5 条候选
> **本文件是自动生成的原始数据**，决策与解读见 `docs/superpowers/specs/ch02-walking-skeleton-design.md`「关键决策记录」；指标释义见 `docs/reports/metrics-glossary.md`。

## 总表

| 策略 | hit@3 | 95% CI | MRR | 相对 dense 不一致对数 |
|---|---|---|---|---|
| dense | 1.00 | 0.90–1.00 | 0.948 | — |
| bm25 | 0.97 | 0.85–0.99 | 0.829 | 1 : 0（不显著） |
| hybrid | 1.00 | 0.90–1.00 | 0.962 | 0 : 0（不显著） |

## 分桶（命中/总数）

| 策略 | hard_invoice | hard_member | hard_product | hard_promo | hard_regulation | hard_returns | hard_shipping |
|---|---|---|---|---|---|---|---|
| dense | 3/3 | 3/3 | 12/12 | 2/2 | 5/5 | 4/4 | 6/6 |
| bm25 | 3/3 | 2/3 | 12/12 | 2/2 | 5/5 | 4/4 | 6/6 |
| hybrid | 3/3 | 3/3 | 12/12 | 2/2 | 5/5 | 4/4 | 6/6 |

## 分问法（命中/总数）

| 策略 | colloquial | formal |
|---|---|---|
| dense | 15/15 | 20/20 |
| bm25 | 15/15 | 19/20 |
| hybrid | 15/15 | 20/20 |

## 共同短板（换策略也救不了的题）

无：每个策略失败的地方都不重叠。

## 失败样本（这些比总分有用）

### bm25（1 条未命中）

- `积分多久过期` → 期望 `member#积分规则`，实际 `returns#赠品与各类优惠形式的处理`
