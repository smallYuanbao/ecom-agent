# Ch07 端到端本地跑分

> 时间：2026-11-08T17:48:50 · 目标：`http://127.0.0.1:8081` · 样本：44

## 总表

**通过 41/44（93.2%）**

| 桶 | 通过 / 总数 | 通过率 |
|---|---|---|
| B1 | 10 / 10 | 100.0% |
| B2 | 8 / 8 | 100.0% |
| B3 | 7 / 8 | 87.5% |
| B4 | 4 / 6 | 66.7% |
| B5 | 4 / 4 | 100.0% |
| B6 | 4 / 4 | 100.0% |
| B7 | 4 / 4 | 100.0% |

## 失败样本

| id | 桶 | query | 失败项 | 实际路由 | 实际回答 |
|---|---|---|---|---|---|
| R06 | B3 | 订单1001 商品完好，不想要了，我要退款 | refund.eligible: True != False; refund.amount: 269900 != 0; answer: 缺少「不符合」; answer: 缺少「7 天\|七天\|超」 | rules | 这个问题我这边处理不了，已经为您转接人工客服。 |
| B03 | B4 | 订单1106 商品完好，不想要了，我要退款 | refund.eligible: True != False; refund.amount: 129900 != 0; refund.reason_code: ok != out_of_window; answer: 缺少「不符合」; answer: 缺少「7 天\|七天\|超」 | rules | 这个问题我这边处理不了，已经为您转接人工客服。 |
| B05 | B4 | 订单1108 我不想要了，拒收退款 | refund.eligible: False != True; refund.amount: 0 != 289900; refund.reason_code: not_applicable != reject_then_refund; answer: 缺少「拒收」; answer: 缺少「2899.00 元」 | rules | 这个问题我这边处理不了，已经为您转接人工客服。 |
