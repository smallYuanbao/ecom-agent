# Ch05 验收报告 · 多轮与上下文 + E 出口只读 ReAct

> 日期：2026-11-06 · 模型：`deepseek-chat` · 库：`data/app.db` · 检索：Chroma + Ollama
> 对应 spec：[ch05-multi-turn-design.md](../superpowers/specs/ch05-multi-turn-design.md)
> 对应 plan：[ch05-multi-turn.md](../superpowers/plans/ch05-multi-turn.md)

## 结论

**通过。** 跨轮接续、park、指代消解、会话锁、只读 ReAct 全部可用。

- 全量测试 **829 passed, 30 skipped**；
- 普通退款轮中位 **3.62s**，最慢 **4.30s**（预算 6s）；
- ReAct 双工具查询中位 **2.59s**，最慢 **2.81s**（预算 15s）。

## 验收清单

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 跨轮退款：补单号后不再问原因 | ✅ | 场景 1；用 1002（1001 是超 7 天考点） |
| 2 | park：改话题后能继续 | ✅ | 场景 2：退货政策 → 继续退费 → 完成 |
| 3 | 串轮污染：闲聊轮无退款字段 | ✅ | 场景 3：只有 `resume_flow` 提示，无金额/订单 |
| 4 | 指代消解：「那单呢」能接回 | ✅ | 场景 4：接回订单 1001 |
| 5 | 窗口预算 + 摘要 | ✅ 单测 | `test_window_*` / `test_summary_*`；演示未构造 20 轮 |
| 6 | `db-{id}` 锚点 | ✅ 单测 | `test_save_and_load_with_anchors` |
| 7 | 30 分钟失效 | ✅ 单测 | `test_expired_flow_is_cleared` |
| 8 | 会话锁 + `busy` | ✅ 单测 | `test_same_conversation_is_serialized` / `test_busy_*` |
| 9 | ReAct 长尾能调工具作答 | ✅ | E 子图直接调用：物流 + 库存双工具 |
| 10 | 写工具不在工具集 | ✅ 静态断言 | `test_whitelist_has_no_write_tools` |
| 11 | 4 步封顶 / 超步收敛 | ✅ 单测 | `test_step_limit_forces_answer` |
| 12 | 每步一行 span | ✅ | `react:step:N` / `react:total`、`coref` |

## 六条演示场景

### 1 · 跨轮退款

```
用户：我要退款，尺寸不合适
系统：我查到您最近有这几笔订单，请问是哪一单？（3 张卡片）
用户：1002
系统：您的订单符合退货条件，本次应退金额 1249.00 元。
      麻烦您拒收包裹，退回仓库后就会全额退款。
```

**用 1002 而不是 1001**：seed 里 1001 是「超过 7 天」的考点。
1001 的模型判定与规则复算分歧（`window_bypass`），按设计转人工，这是正确行为。

### 2 · park 再回来

```
用户：我要退款
系统：请提供订单号（候选卡片）
用户：退货政策是什么
系统：退货政策主要分几块…（引用 [1][3][4]）
      （您还有一笔未完成的退款申请，需要继续吗？）
用户：继续退费
系统：请提供订单号（候选卡片）
用户：1002
系统：请问您想退货的原因是什么？
用户：尺寸不合适
系统：可以退，本次应退金额 1249.00 元。
```

### 3 · 串轮污染

```
用户：我要退款
系统：请提供订单号（候选卡片）
用户：你好
系统：在的，请问有什么可以帮您？
      （您还有一笔未完成的退款申请，需要继续吗？）
```

本轮**没有退款金额、订单号、judge 字段**；`resume_flow` 是 park 的提示动作，不是退款动作。

### 4 · 指代消解

```
用户：订单1001 到哪了
系统：快递 SF1001001：最新状态 已签收。最近轨迹…
用户：那单呢
系统：快递 SF1001001：最新状态 已签收。最近轨迹…
```

第二轮 `coref` 把「那单」补成「订单1001 的物流到哪了」，走 B 出口。

### 5 · 多工具长尾

端到端：

```
用户：订单1001 的物流到哪了，顺便看下 ST-20 还有货吗
系统：快递 SF1001001：最新状态 已签收…
```

分类为「物流查询」→ B 出口，只答物流，**库存那一半被丢掉**。
这是 P5-11 多意图未做的已知限制，不是 ReAct 的问题。

直接调用 E 子图（跳过分类）验证 ReAct：

```
answer: 两件事都查到了：
        1. 订单 1001 物流：SF1001001 已签收…
        2. ST-20 库存：石墨灰，42 件，1299.00 元
```

调用了 `query_logistics` + `query_stock` 两个工具。

### 6 · 问候

```
用户：你好
系统：在的，请问有什么可以帮您？
```

`needs_tools=false`，不进 ReAct。

## 验收中发现并修复的问题

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | 第一轮说了原因，第二轮仍问原因 | C 出口缺订单号时直接 `need_info`，跳过了 `extract` | 轮末 `_capture_refund_draft`：问订单号且 draft 缺原因时补一次抽取 |
| 2 | 连续追问几次后直接转人工 | `validate_retry` 把「继续」「补单号」也当失败累加 | 槽位命中 / 恢复轮把 `validate_retry` 重置为 0 |
| 3 | ReAct 把 129900 当成元 | 工具返回的是分，模型直接念 | `amount_text` / `price_text` 进回灌结果，原始分值移除 |
| 4 | `try.py chat` 不显示回答 | delta 帧没有 `event` 字段，判断分支永远不命中 | 改成检查 `"delta" in frame` |
| 5 | 演示问题「几点上班」没有答案 | KB 没有营业时间条目 | 验收改用「退货政策是什么」 |

## 延迟

全部真实请求，n=5：

| 场景 | min | median | max | 预算 |
|---|---|---|---|---|
| 退款首轮（问订单号） | 2.00s | **2.15s** | 2.21s | 6s |
| 退款完成轮（判定 + 算钱 + 回复） | 3.39s | **3.62s** | 4.30s | 6s |
| ReAct 双工具查询 | 2.20s | **2.59s** | 2.81s | 15s |

摘要触发轮未在演示中构造；单测覆盖超时与失败降级，超时上限 3s。

## 已知限制

| 限制 | 现状 | 计划 |
|---|---|---|
| 多意图 | 一句话两个诉求只处理主诉求（场景 5） | P5-11 |
| 自由表达指代 | 「第一单」「那个推车」抽不到；当前只认订单号/SKU/点选 | P5-14：ReAct resolver + 商品目录 |
| ReAct 范围 | 只在 E；无商品名→SKU、无优惠券、无写操作 | 按需 |
| ReAct 引用 | 只有 `search_kb` 有 chunk id；订单/库存回答无引用 | 可接受 |
| 会话锁 | 单进程有效 | 多实例前换 Redis/DB 锁 |
| 流式 | SSE 帧流式，答案整段一次 `delta` | 按需 |
| 摘要 | 单测覆盖，演示未构造 20 轮 | 长对话验证 |
| 营业时间 | KB 无此条目 | 补语料后再演示 |
| 20 轮窗口 | 未做端到端长对话 | Ch07/08 可补 |

## 测试分布

```
uv run pytest -q → 829 passed, 30 skipped
```

| 模块 | 覆盖 |
|---|---|
| `tests/core/test_history.py` | 消息、窗口、摘要 |
| `tests/core/test_coref.py` | 指代消解 |
| `tests/core/test_session_state.py` | 跨轮状态、TTL、park 决策 |
| `tests/core/test_slot_fill.py` | 槽位填充 |
| `tests/core/test_lock.py` | 会话锁 |
| `tests/core/test_react.py` | ReAct harness |
| `tests/core/test_llm_tool_chat.py` | 工具调用解析 |
| `tests/api/test_chat_sse.py` | 一轮生命周期、resume、park、busy |
| `tests/graph/test_fallback_agent.py` | E 出口 gate / ReAct / 脚本 |
| `tests/graph/test_resume.py` | 意图短路、skip_extract |

## 复现

```bash
uv run pytest -q

# 六条场景
uv run python scripts/try.py chat "我要退款，尺寸不合适"   # 再输入 1002
uv run python scripts/try.py chat "我要退款"              # 退货政策是什么 → 继续退费 → 1002 → 尺寸不合适
uv run python scripts/try.py chat "我要退款"              # 再输入 你好
uv run python scripts/try.py chat "订单1001 到哪了"        # 再输入 那单呢
uv run python scripts/try.py chat "你好"
```
