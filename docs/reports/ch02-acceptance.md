# Ch02 验收报告（主流程骨架）

> 验收日期 2026-11-01 ｜ 语料 `kb_version=a8b084ac29b9`（41 chunk）
> 对应 spec：[ch02-walking-skeleton-design.md](../superpowers/specs/ch02-walking-skeleton-design.md)

## 一、验收项逐条对照

| # | spec 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | `POST /api/chat` 走图，响应有 `route`，落在五个出口之一 | ✅ | 下表 7 条请求全部命中期望出口 |
| 2 | 知识类 → 答案 + `citations`，引用能在库中查到 | ✅ | 「退货政策是什么」返回 5 条引用 |
| 3 | 规则类 → 明确告知开发中，**响应中不得出现任何金额** | ✅ | 正则 `\d+\s*(元\|块)` 无命中 |
| 4 | 投诉/要人工 → 安抚话术 + `transfer_human` 动作 | ✅ | 见下方帧明细 |
| 5 | 闲聊 / 致谢 / 无法处理 → 三个脚本各自命中 | ✅ | 「在吗」「谢谢」「你们老板是谁」各命中不同脚本 |
| 6 | 每次请求 ≥3 条 `trace_span`，`trace_id` 一致 | ✅ | 每次 4 行（3 节点 + total） |
| 7 | `/api/meta` 返回 `kb_version` | ✅ | 返回 `a8b084ac29b9` |

## 二、端到端请求（7 条）

| 输入 | route | 帧序列 | 金额 | 引用 | 动作 | 耗时 |
|---|---|---|---|---|---|---|
| 退货政策是什么 | knowledge | ack/route/delta/citations/done | 无 | 5 | 0 | 7.8s |
| 订单 1001 到哪了 | data | ack/route/delta/actions/done | 无 | 0 | 1 | 0.8s |
| 我这单能退多少 | rules | ack/route/delta/actions/done | **无** | 0 | 1 | 1.1s |
| 你们这什么破服务 | human | ack/route/delta/actions/done | 无 | 0 | 1 | 0.9s |
| 在吗 | fallback | ack/route/delta/done | 无 | 0 | 0 | 0.9s |
| 谢谢 | fallback | ack/route/delta/done | 无 | 0 | 0 | 1.0s |
| 你们老板是谁 | fallback | ack/route/delta/actions/done | 无 | 0 | 1 | 0.7s |

规则类实际回答：

```
退费核算正在开发中，您可以先转人工处理。
```

**没有任何数字**——这是本章最要紧的一条：占位绝不能让人误以为是真实报价。

## 三、意图分类准确率（供 Ch04 调边界）

12 类各一句典型问法，**实测 12/12 = 100%**，置信度 0.90–0.95。

| 意图 | 问法 | 判定 |
|---|---|---|
| 政策问答 | 你们的退货政策是什么？ | ✓ 0.95 |
| 商品规格 | ST-60 有多重，折叠后多大？ | ✓ 0.95 |
| 优惠券活动 | 满 300 减 50 的券能跟会员折扣一起用吗？ | ✓ 0.90 |
| 会员权益 | 会员积分怎么算，可以抵现吗？ | ✓ 0.92 |
| 订单查询 | 帮我看看订单 1001 的状态 | ✓ 0.95 |
| 物流查询 | 我的快递到哪了？ | ✓ 0.95 |
| 库存价格 | ST-20 还有货吗？多少钱？ | ✓ 0.95 |
| 退款申请 | 订单 1001 我要退货 | ✓ 0.95 |
| 价保申请 | 我上周买的推车降价了，能补差价吗？ | ✓ 0.95 |
| 订单变更 | 订单 1001 能改一下收货地址吗？ | ✓ 0.95 |
| 投诉 | 你们这什么破服务，等了三天都没人管 | ✓ 0.90 |
| 闲聊 | 在吗 | ✓ 0.95 |

**注意**：这是 12 条"标准问法"，不代表真实分布。Ch04 之后要用更难的边界样本再调一轮，
本次结果只作为基线记录。

## 四、知识出口指标（42 条查询）

见 [knowledge-eval.md](./knowledge-eval.md)，关键数字：

| 指标 | 值 |
|---|---|
| 引用覆盖率 | **42/42 = 100%** |
| 要点全覆盖率 | **40/40 = 100%** |
| 闸放行率 | 42/42 = 100% |

> **时效说明**：本表是 Ch02 验收时（`kb_version=a8b084ac`）的快照。
> 后续补「已发货未收到」政策后语料变更，重跑为 **39/40 = 98%**。
> 以 [knowledge-eval.md](./knowledge-eval.md)（`2f59fbaa`）为准。

## 五、迷你质量传导表（Task 10 要求）

**质量传导**指"检索质量会不会传到答案质量上"。四格：

|  | 答案覆盖全部要点 | 答案缺要点 |
|---|---|---|
| **检索命中** | 40 | 0 |
| **检索未命中** | **0** | 0 |

**最危险的一格（检索未命中但答案完整）为空**——这一批里没有出现"没找到依据却答得很完整"的
编造迹象。

### 但这一格为空，是因为检索根本没失手

42 条查询的 hit@3 是 100%（见 [retrieval-eval.md](./retrieval-eval.md)）。
**没有"未命中"的样本，就无法验证"未命中时会不会编"。**

所以这条证据是**弱**的：它只说明"这批题太简单"，不能说明"系统不会编造"。

真正验证需要：
- 构造检索必然失手的样本（语料里确实没有、但话题相近的问题）
- 或把闸阈值调高，人为制造"闸拒绝"的批次

**这条留给 Ch07 的 P7-7。** 本章记录结论时就写"未观测到"，不写"已验证不存在"。

## 六、trace 证据

一次请求产生 4 行 `trace_span`，共享同一 `trace_id`：

| span | 耗时 |
|---|---|
| `node:resolve_reference` | 2 ms |
| `node:classify_intent` | 907 ms |
| `node:rules` | 4 ms |
| `turn:total` | 917 ms |

节点之和 ≈ 总耗时，说明计时口径一致。

耗时分布（`node:knowledge` 最高）：

| span | 最长耗时 |
|---|---|
| node:knowledge | 8805 ms |
| node:knowledge | 6480 ms |
| node:knowledge | 2403 ms |

**知识出口是唯一的慢路径**（向量化 + 检索 + 生成）。C5 的 30s 预算目前有充足余量，
但 Ch07 加评测并发后要重新测。

## 七、本章没做 / 已知局限

| 项 | 状态 | 归属 |
|---|---|---|
| B 数据出口（订单/物流/库存查询） | 占位 | Ch04 |
| C 规则出口（退费核算） | 占位 | Ch03 |
| 多轮上下文、指代消解 | 透传 | Ch05 |
| 会话归属校验（`not_found` / `forbidden`） | 未接 | Ch06 |
| 输出校验（金额/措辞） | 未做 | Ch06 |
| 生成按 token 流式 | 一次性 `delta` | 后续优化 |
| 「能分期付款吗」漏过证据闸 | 已知，由生成侧兜住 | 见 gate.py |

## 八、复现方式

```bash
uv run python scripts/init_db.py && uv run python scripts/build_kb.py
uv run uvicorn app.main:app --port 8080 --log-level warning

curl -sN -X POST localhost:8080/api/chat -H 'content-type: application/json' \
     -d '{"message":"退货政策是什么"}'

uv run python scripts/eval_retrieval.py --out docs/reports/retrieval-eval.md
uv run python scripts/eval_gate.py      --out docs/reports/gate-eval.md
uv run python scripts/eval_knowledge.py --out docs/reports/knowledge-eval.md
uv run pytest -q
```
