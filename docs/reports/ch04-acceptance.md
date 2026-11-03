# Ch04 验收报告 · B 出口（数据查询）

> 日期：2026-11-04 · 模型：`deepseek-chat`（只用于上游意图分类）· 库：`data/app.db`（20 单）
> 对应 spec：[ch04-data-exit-design.md](../superpowers/specs/ch04-data-exit-design.md)
> 对应 plan：[ch04-data-exit.md](../superpowers/plans/ch04-data-exit.md)

## 结论

**通过。** 五个出口全部是真实实现，占位机制已删除。

- B 出口内部**零模型调用**：参数用正则 + 数据库匹配，回答用模板；
- 三类查询各有一条 happy path，失败路径按"用户能否补"分流；
- 端到端中位数 **0.98s**（预算 30s），比 C 出口快一个数量级。

## 验收清单

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | `pytest` 全绿 | ✅ **706 passed, 39 skipped** | `uv run pytest -q` |
| 2 | 订单 / 物流 / 库存各一条 happy path | ✅ | 见下「三条实测」 |
| 3 | 缺单号列候选 | ✅ | 3 个 `order_candidates` + `select_order` |
| 4 | 越权与不存在同一句 | ✅ | 都是「没有找到这笔订单…」+ 同一批候选 |
| 5 | 四种货态文案 | ✅ | `on_sale` 有货/暂时无货、`sold_out`、`presale` |
| 6 | 待发货查物流不报错 | ✅ | 「订单 1003 当前状态：待发货，暂无物流信息。」 |
| 7 | 每个节点一行 span | ✅ | `trace_span` 里 `data:extract_params/validate/query/format/need_info` |
| 8 | 模型调用次数 = 0（分类除外） | ✅ | `test_data_exit_does_not_import_llm`：模块不依赖 `app.core.llm` |
| 9 | `00-tasks.md` P4 | ✅ P4-1 / P4-2 / P4-4 / P4-5 / P4-6 完成；P4-3 建单仍待做 |

## 三条实测

| 输入 | 路由 | 回答 |
|---|---|---|
| `订单1001 到哪了` | data / 物流 | 快递 SF1001001：最新状态 已签收。最近轨迹：10-01 14:22 已签收 本人签收；09-30 09:00 已揽收 杭州转运中心已收件。 |
| `订单1002 什么状态` | data / 订单 | 订单 1002：状态 已发货；实付 1249.00 元；下单时间 2026-10-25 09:00。物流单号 SF1001002。 |
| `ST-20 还有货吗` | data / 库存 | ST-20 石墨灰：有货（42 件），售价 1299.00 元。 |

## 边界场景

| 输入 | 结果 |
|---|---|
| `我要查订单`（无单号） | 列最近 3 单 + `actions.type=select_order` |
| `订单9999 什么状态`（不存在/越权） | 「没有找到这笔订单，您可以选一笔最近的订单：」+ 同一批候选 |
| `订单1003 到哪了`（待发货） | 「订单 1003 当前状态：待发货，暂无物流信息。」 |

**越权与不存在走完全相同的路径**：仓库层同一个 `NotFound`，工具层同一个 `not_found`，
上层同一句话。攻击者无法从回答差异里筛出真实订单号。

## 延迟

5 轮真实请求，全部 `route=data`：

```
1.55s  订单1001 到哪了
0.99s  订单1002 什么状态
0.81s  ST-20 还有货吗
0.87s  订单1003 到哪了
0.98s  订单1004 什么状态

n=5  min=0.81s  median=0.98s  max=1.55s
```

只有上游意图分类调一次模型；B 子图本身是纯数据库查询 + 模板。

## 测试

`tests/graph/data` 共 **71 条**：

| 文件 | 覆盖 |
|---|---|
| `test_graph.py` | 5 节点 / 8 边 / 守卫键 / 可达性 / 空走终止 / span / 零 LLM 依赖 |
| `test_extract_params.py` | 订单号 / SKU 提取；按意图校验；图上的追问效果 |
| `test_need_info.py` | 订单 / 型号 / 规格三种候选；不存在话术；降级 |
| `test_query.py` | 三类查询、规格匹配、失败分流 |
| `test_format.py` | 三个模板、四种货态、轨迹裁剪 |

另：`tests/tools/test_orders.py` 19 条测工具层；`tests/graph/test_order_select.py` 8 条测共享候选。

## 已知限制（不阻塞本章）

| 限制 | 现状 | 计划 |
|---|---|---|
| 多轮补参 | 用户回传单号/型号才能接上；说"第一单"不行 | Ch05 槽位填充（P5-4） |
| 商品名 → SKU | 只认 `ST-20` 这种型号；说"推车"会列型号候选 | 需要商品目录，当前数据没有 |
| D 出口建单 | 话术已有，`create_ticket` 未做 | P4-3 |
| 库存并发 | 查询时刻的库存，不预留不下单 | 本章只读 |

## 复现

```bash
uv run pytest -q
uv run pytest tests/graph/data -q
uv run python scripts/try.py graph "订单1001 到哪了"
uv run python scripts/try.py graph "ST-20 还有货吗"
uv run python scripts/try.py graph "订单9999 什么状态"
```
