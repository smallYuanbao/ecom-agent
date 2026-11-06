# Ch05 · 多轮与上下文 · 改动记录

> 对应 spec：[ch05-multi-turn-design.md](../superpowers/specs/ch05-multi-turn-design.md)
> 对应 plan：[ch05-multi-turn.md](../superpowers/plans/ch05-multi-turn.md)
> 格式说明见 [00-change-log-overview.md](./00-change-log-overview.md)

| # | 日期 | 任务 | commit |
|---|---|---|---|
| 1 | 2026-11-04 | Task 1 · 会话生命周期 + 消息落库 | `cd8b56b` |
| 2 | 2026-11-05 | Task 2 · 历史窗口与滚动摘要 | 未提交 |
| 3 | 2026-11-05 | Task 3 · 指代消解 | 未提交 |
| 4 | 2026-11-05 | Task 4 · 跨轮状态与 30 分钟失效 | 未提交 |
| 5 | 2026-11-05 | Task 5 · 槽位填充 | 未提交 |
| 6 | 2026-11-05 | Task 6 · resume 路由 | 未提交 |
| 7 | 2026-11-05 | Task 7 · park（改话题保留） | 未提交 |
| 8 | 2026-11-06 | Task 8 · 会话级锁 | 未提交 |
| 9 | 2026-11-06 | Task 9 · 只读 ReAct harness | 未提交 |
| 10 | 2026-11-06 | Task 10 · E 出口接入 ReAct | 未提交 |
| 11 | 2026-11-06 | Task 11 · 验收 | 未提交 |

---

## 1 · Task 1 · 会话生命周期 + 消息落库（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/history.py` | `save_user_message` / `save_assistant_message` / `load_messages`；`db-{id}` 锚点；meta 解析 |
| `tests/core/test_history.py` | 5 条：锚点、幂等、点选空消息、error 状态、坏 meta |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `api/chat.py` · `run_turn` | 每轮只用传入的 `conversation_id`，不建会话、不落消息 | 先建/取会话（归属校验）→ 落用户消息 → 跑图 → 落助手消息 | 下一轮要能回放，必须先把这轮写下来 |
| `api/chat.py` · `ack` | `{"event": "ack"}` | 带 `conversation_id` / `user_message_id` | 前端需要拿到会话 id 才能发下一轮 |
| `api/chat.py` · `done` | `{"event": "done"}` | 带 `message_id` | 前端/审计需要知道助手消息落在哪一行 |
| `api/chat.py` · 异常路径 | 直接上抛，用户消息悬空 | 补一条 `status="error"` 的助手消息再上抛 | 回放时不能只有问、没有答 |
| `main.py` · `ChatRequest` | `message: str` 必填 | 增加可选 `selected_action`；`message` 默认空串 | Ch04 的选单卡片需要结构化回传，不能只靠拼文本 |
| `main.py` · `chat` | 只传 message / conversation_id | 透传 `client_message_id` / `selected_action` | K4 幂等与 Ch05 槽位填充都需要 |
| `scripts/try.py` | 只有单轮 `graph` 模式 | 增加 `chat` 交互模式，复用 `conversation_id` | 手工验证跨轮比一直跑单轮图直观 |

### 四个设计点

**1. 用户消息与助手消息分开事务。**
用户消息在跑图前提交：即使后面模型超时，审计里也能看到用户问了什么。
助手消息在跑图后写；失败时补一条 `status="error"`，避免「有问无答」。

**2. 锚点用 DB 主键，不用序号。**
`id=f"db-{row.id}"`。后面滑窗/摘要边界都用它，消息补录或删除不会错位。

**3. `session` 可注入。**
沿用仓库层约定：生产不传，测试传临时库；否则这层只能打桩，测不到真实 SQL。

**4. `selected_action` 存在用户消息的 `meta` 里。**
结构化点选可以没有文字内容；把原始动作存进 meta，回放时能还原用户点了什么。

### 验证

- `uv run pytest -q` → **716 passed, 39 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/core/test_history.py` + `tests/api/test_chat_sse.py` → 25 passed

---

## 2 · Task 2 · 历史窗口与滚动摘要（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/prompts.py` · `SUMMARY_SYSTEM` | 摘要 prompt：只留事实，不写推测与客套 |
| `tests/core/test_history.py` | 追加 6 条：裁窗、工具压缩、摘要推进、摘要失败、边界回退 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `history.py` · `load_window` | 只有全量 `load_messages` | 按预算构建窗口；超预算先摘要、再截断 | 全量塞给模型，长对话会爆窗口 |
| `history.py` · `maybe_summarize` | 无 | 最老一半压成摘要；失败保留旧摘要 | 只截断会丢早期事实（订单号、诉求） |
| `repo_conversation.py` · `set_summary` | 无 | 写摘要 + 边界，**只允许前移** | 并发/重试时旧任务不能把边界拉回去 |
| `state.py` · `ConversationState` | 无 `history` / `summary` | 增加两个字段 | 指代消解与生成需要历史，不能只靠消息表 |
| `chat.py` · `run_turn` | 只传本轮 `raw_query` | 先读窗口，再落用户消息；state 带 `history` / `summary` | 构建上下文需要上一轮及更早的消息 |

### 五个设计点

**1. 预算按块分配，不用全局 70%。**
系统提示 / 证据 / 摘要 / 输出各留一块，剩下的才是历史预算。
以后加证据条数或改提示，不会悄悄把历史挤没。

**2. 窗口不含本轮用户消息。**
`load_window` 在落用户消息之前调用；本轮输入由 `raw_query` 单独传。
否则同一句话会在 `history` 和 `raw_query` 里出现两次。

**3. 至少保留最近 2 轮原文。**
`MIN_KEEP_MESSAGES = 4`。宁可略微超预算，也不把当前上下文裁没。

**4. 摘要边界只前移。**
`set_summary` 比较 `upto_msg_id`，旧任务/并发请求不能回退。

**5. 摘要失败可降级。**
3s 超时；失败保留旧摘要，窗口继续按预算截断，本轮不阻断。

### 验证

- `uv run pytest -q` → **724 passed, 37 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/core/test_history.py` → 11 passed

---

## 3 · Task 3 · 指代消解（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/coref.py` | `resolve(query, history)`：三轮改写 + 三个短路 + 结果守卫 |
| `tests/core/test_coref.py` | 10 条：改写、摘要行、无历史、含实体、格式错误、异常、过短/过长、原样 |
| `tests/graph/test_resolve_reference.py` | 3 条：节点改写 + span、resume 透传、无 trace 不写 span |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `nodes.py` · `resolve_reference` | 透传 `resolved_query = raw_query` | 调 `coref.resolve`；resume 轮直接透传 | 「那单」「这个」不结合历史，意图和检索都会偏 |
| `prompts.py` · `COREF_*` / `coref_messages` | 无 | 指代消解 prompt + 结构化 schema | 需要模型只改写、不回答 |
| `state.py` · `ConversationState` | 无 `resume_route` / `resumed` | 两个字段 | 节点要能识别 resume 轮，并把是否恢复传给下游 |

### 四个设计点

**1. 三个零成本短路。**
历史为空、原句已含订单号/SKU、resume 轮——都不调模型。
前两个是「确定不需要」，第三个是「补槽位不是改写」。

**2. 改写失败不阻断。**
`LLMFormatError` / 5s 超时 / 其它异常一律回落原句，mode 记 `passthrough_error`。
改写是增强项，不是流程前提。

**3. 结果守卫防「扩写」。**
空、过短、超过 `max(2×原长, 原长+24)`、或只差空白 → 用原句。
短句（「那单呢」）补全后本来就会长，所以留了 24 字的绝对增量。

**4. 留痕区分改了没改。**
`coref` span 记 `mode` / `history_count` / `summary_used` / 改写前后各 200 字。
长期全是 `passthrough` 就说明改写没起作用，该查 prompt 或关掉。

### 参考实现补充（Task 3 收尾）

对照 MewHelp 的 `COREF_REWRITE_SYSTEM`，补了三处：

| 补充 | 为什么 |
|---|---|
| **摘要行进 coref 上下文** | 跨滑窗的「最开始那个订单」只能靠摘要兜住；之前只给了最近几条消息 |
| **Prompt 加三条业务规则** | ① 指代订单时必须带订单号，不能只补商品名；② 通用政策问题不要硬塞订单号/商品名；③ 口语/情绪归一成标准问法（spec M3 的「指代 + 口语归一」之前没落地） |
| **span 加 `history_count` / `summary_used`** | 改写出错时能区分「历史太少」还是「摘要没带上」 |

Prompt 改成「摘要 + 最近对话 + 当前问句」的单条 user 消息（带角色标签），
比原来的多条 role 消息更容易塞摘要，也更接近参考实现。

### 验证

- `uv run pytest -q` → **740 passed, 37 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- 指代 / 节点 → 15 passed

---

## 4 · Task 4 · 跨轮状态与 30 分钟失效（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/session_state.py` | `FlowState`、`load_flow`、`save_flow_from_state`、`draft_from_state`、`is_expired` |
| `scripts/cleanup.py` | 批量清理过期流程 |
| `tests/core/test_session_state.py` | 10 条：读写、过期清空、draft 形状、完成清空、批量清理 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `chat.py` · `run_turn` | 不读也不写 `conversations` 的流程字段 | 跑图前 `load_flow`（惰性清过期），轮末 `save_flow_from_state` | 下一轮要接回流程，必须先把「在等什么」存下来 |
| `chat.py` · 助手消息事务 | 只写助手消息 | 助手消息 + 流程状态**同一事务** | 不能出现「消息写了、流程没写」的半截状态 |
| `chat.py` · 落库异常 | 只记日志 | 同时把 `message_id` 置空 | 事务回滚后不能把已 flush 的自增 id 发给前端 |
| `session_state.py` · `load_flow` | 内部固定再查一次 `get_conversation` | 可传 `conversation` 复用；传了就不再查 | 同一请求对同一行查两次，白一次 DB 往返 |

### 四个设计点

**1. 只存三个字段。**
`active_flow` / `pending_question` / `draft`。其余 state 都是一轮内中间产物，不落库。

**2. draft 用嵌套形状。**
外层 `intent` / `order_id` / `validate_retry`，内层 `refund`。
恢复时 `state["refund_draft"] = draft["refund"]`，不会把元信息混进判定 prompt。
结构由 `FlowDraft` / `RefundDraft` 两个 `TypedDict` 标出（仅类型说明，运行时仍是 dict）。

**3. TTL 读时惰性判断 + 脚本批量清理。**
`load_flow` 发现超过 30 分钟就清空并返回 `expired=True`；`scripts/cleanup.py` 做批量卫生。
注意 TTL 按「最后活动时间」刷新，不是「park 时间」——长对话可能让旧流程不过期，已写进 spec 风险表。

**4. 当前知识/兜底轮会清空流程。**
这是 Task 4 的简化规则；Task 7 的 park 会改成「保留旧流程 + 提示用户继续」。

**5. 同一请求只查一次会话。**
`run_turn` 已经用 `get_conversation` 做过归属校验，`load_flow` 直接接收这个对象，
不再内部重查；只有调用方没传 `conversation` 时才用 `user_id` 自己查。

### 验证

- `uv run pytest -q` → **754 passed, 35 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/core/test_session_state.py` + `tests/api/test_chat_sse.py` → 32 passed

---

## 5 · Task 5 · 槽位填充（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/slot_fill.py` | `SlotResult`、取消/恢复词、`fill()`：点选 / 正则 / 规格透传 / 退款抽取 |
| `tests/core/test_slot_fill.py` | 18 条：订单号（含全角）、SKU、规格、取消、恢复、点选、退款合并、失败、无槽位不产生信号 |

### 修改与原因

本 Task 只新增纯函数模块，不改现有调用方；接回逻辑在 Task 6。

### 五个设计点

**1. 判定顺序固定。**
先判 `pending_question`：没有待补槽位就直接返回，**不产生 cancel / resume 信号**。
有槽位时再按 取消 → 恢复 → 结构化点选 → 抽槽位 → 都没命中。
先判取消/恢复，避免用户说「算了」还被拿去抽订单号。

**2. 订单号做全角归一。**
`１００１` → `1001`；SKU 统一转大写。
前端点选可能传数字 `1001` 或小写 `st-20`，也在这里统一成下游形状。

**3. 规格透传。**
规格是开放集合，`slot_fill` 不查库；原样返回，匹配交给 `data_exit.query` 的最长命中。

**4. 退款要素合并而非覆盖。**
复用 C 出口的 `extract_draft`（它已经丢弃非法字段）；
新值覆盖旧值，但只覆盖抽取器认可的字段——`condition_ok=false` 能覆盖旧的 `None`。

**5. 失败不抛。**
抽取异常 / 没抽到 → `filled=False`，由调用方按新话题处理（park）。

### 验证

- `uv run pytest -q` → **773 passed, 34 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/core/test_slot_fill.py` → 18 passed

---

## 6 · Task 6 · resume 路由（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `tests/graph/test_resume.py` | 3 条：意图短路、未知出口回落、skip_extract 不调模型 |
| `tests/api/test_chat_sse.py` | 6 条：文本补单号、带新信息、点选、取消、数据流程、退款原因不二次抽取 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `routing.py` · `ROUTE_NAMES` | 只在 `build.py` 有一份出口清单 | 抽成单一来源；`build.EXIT_NODES` 直接引用 | `classify_intent` 也要判断 resume 值是否合法，不能再抄一份 |
| `nodes.py` · `classify_intent` | 每轮都调模型分类 | `resume_route` 合法时直接返回该出口，`resumed=True` | 用户回「1001」不是新意图，再分类会被判成「其他」 |
| `rules_exit.py` · `extract` | 恢复轮也会对「1001」调一次抽取模型 | `skip_extract` 时直接沿用 `refund_draft` | 纯补单号没有新要素，这次调用纯浪费 |
| `state.py` · `ConversationState` | 无 `skip_extract` | 增加该字段 | 抽取节点需要知道这轮要不要跳过 |
| `chat.py` · `run_turn` | 只加载流程，不使用 | 槽位填充 → 恢复 draft / order_id / sku_id / validate_retry → 写 `resume_route` | 不把 draft 塞回 state，出口还是会重新问 |

### 五个设计点

**1. 短路放在 `classify_intent`，不新增入口边。**
主图形状不变，前四章的结构测试继续有效；只在节点内部提前返回。

**2. 恢复字段一次写全。**
`intent` / `order_id` / `sku_id` / `refund_draft` / `validate_retry`。
`validate_retry` 不恢复的话，每轮都从 0 开始，追问次数限制会失效。

**3. 槽位值覆盖 draft 里的旧值。**
`order_id` / `sku_id` 用本轮点选或文本抽到的值；退款要素用合并后的 draft。
规格透传，不写 state，交给数据出口匹配。

**4. `skip_extract` 只在不需要二次抽取时跳过。**
- 点选 / 空文本 → 跳过；
- 「1001」→ 跳过；
- 退款原因槽位（`reason_code` / `refund_extract`）→ 跳过，`slot_fill` 已经抽过了；
- 「继续退费」→ 跳过（没有新信息）；
- 「1001，尺寸不合适」→ **不跳过**，仍要抽取并合并。

**5. draft 损坏不硬接。**
`draft["intent"]` 不在 `INTENT_TO_ROUTE` 里就不设 `resume_route`，
按正常分类走——宁可重新问，也不把流程接到错误的出口。

### 验证

- `uv run pytest -q` → **782 passed, 34 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/graph/test_resume.py` + `tests/api/test_chat_sse.py` → 30 passed

---

## 7 · Task 7 · park（改话题保留）（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `tests/core/test_session_state.py` | 追加 5 条：keep / clear on human / resumed 完成 / 新流程替换 / 无 parked |
| `tests/api/test_chat_sse.py` | 改 1 条并补断言：知识轮 park 保留、提示、`resume_flow` 动作；转人工才清空 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `session_state.py` · 轮末写入 | 完成/知识/兜底一律清空 | 新增 `FlowDecision` + `decide_flow`：存 / 保留 / 清空 | 用户问一句营业时间，退款进度就没了 |
| `session_state.py` · `save_flow_from_state` | 直接写库 | 兼容旧调用，内部改成「决策 + 应用」 | 需要先决策再决定是否给回答加提示 |
| `chat.py` · 回答组装 | 直接发 `final["answer"]` | park 时追加「您还有一笔未完成的…」+ `resume_flow` 动作 | 用户要知道旧流程还在 |
| `chat.py` · `turn:total` span | 只有路由/答案/引用 | 加 `parked` / `flow_replaced` | 出现流程替换时能事后归因 |

### 五个设计点

**1. 决策与落库分开。**
`decide_flow` 是纯函数，在发 `delta` 之前调用——提示必须先拼进回答。
真正写库仍在助手消息的同一事务里。

**2. 三种结果。**

| 终态 | 动作 |
|---|---|
| 新流程有 `pending_question` | `save`：存新流程 |
| 旧流程还在、本轮没接回、不是转人工 | `keep`：保留旧流程 + 提示 |
| 本轮接回并完成 / 取消 / 转人工 | `clear` |

**3. 新流程替换旧流程。**
`replaced_flow` 非空时记日志与 span；同一会话只保留一个 `active_flow`。

**4. 提示只在本轮加一次。**
文案：`（您还有一笔未完成的退款申请，需要继续吗？）`
动作：`{"type": "resume_flow", "flow": "refund"}`。

**5. 取消不 park。**
`slot.cancel` 时传 `parked=None`，已经 `clear_flow`，不会再被保留。

### 验证

- `uv run pytest -q` → **788 passed, 33 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/core/test_session_state.py` + `tests/api/test_chat_sse.py` → 43 passed

---

## 8 · Task 8 · 会话级锁（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/lock.py` | `BusyError`、`conversation_lock`：按会话串行、5s 超时、引用计数清理 |
| `tests/core/test_lock.py` | 5 条：同会话串行、超时 busy、不同会话独立、锁表清理、可重入 |
| `tests/api/test_chat_sse.py` | 追加 1 条：持锁时 `run_turn` 抛 `busy` |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `chat.py` · `run_turn` | 整轮裸跑 | 先拿锁，再调 `_run_turn_locked` | 两条并发会互相覆盖 `pending_question` / `draft` |
| `chat.py` · 轮内主体 | 读会话、读流程、跑图混在一起 | 拆出 `_run_turn_locked`，**锁内再读会话** | 先读后拿锁会读到「上一轮还没写库」的旧状态 |

### 五个设计点

**1. 锁粒度 = `conversation_id`。**
不同会话完全并行；同会话第二条最多等 5s，超时返回 `busy`。

**2. 锁覆盖整个流式输出。**
`async with conversation_lock(cid): async for frame ... yield`；
客户端断开时生成器关闭，`finally` 释放。

**3. 先拿锁，再读会话。**
如果先 `get_conversation` 再等锁，等锁期间上一轮可能已经改了 summary / 流程，
第二条请求会拿着旧对象执行。现在 `get_conversation` 和 `load_flow` 都在锁内。

**4. 锁表引用计数清理。**
`cid -> (loop, lock, refcount)`；最后一个使用者释放时移除，避免锁表无限增长。
按事件循环区分，pytest 多循环不会复用旧锁。

**5. 单进程有效。**
多 worker / 多实例要换 Redis 或 DB advisory lock——已写进模块 docstring 与已知限制。

### 验证

- `uv run pytest -q` → **794 passed, 33 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/core/test_lock.py` + `tests/api/test_chat_sse.py` → 33 passed

---

## 9 · Task 9 · 只读 ReAct harness（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/react.py` | `ToolSpec`、4 个白名单工具、执行管道、4 步循环、span |
| `tests/core/test_react.py` | 14 条：直接回答、一次工具、步数封顶、错误回灌、引用、白名单、参数归一、截断、handler 完整性、int 钳制 |
| `tests/core/test_llm_tool_chat.py` | 3 条：解析 tool_calls、空工具强制回答、坏 JSON 降级 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `llm.py` · 新增 `tool_chat` | 只有 `chat` / `structured_call`，不会跑工具循环 | 一步工具调用：带 tools 返回 `ToolCall`，不带 tools 强制回答 | ReAct 需要多轮「模型 → 工具 → 模型」 |
| `llm.py` · `ToolCall` / `ToolChatReply` | 无 | 把 OpenAI SDK 类型收口成 dataclass | 调用方不直接依赖 SDK 对象，测试可构造 |

### 六个设计点

**1. 只读白名单写死。**
`TOOL_WHITELIST` 只有 4 个查询工具；`create_ticket` / `submit_refund` 连 schema 都不给模型看。

**2. 固定执行管道。**
白名单 → 必填校验 → 丢弃未知参数 / `user_id` → 类型归一 →
`asyncio.wait_for` → `ToolResult` → 截断 → span。

**3. 按决定：不引 `jsonschema`、不做重试。**
只检查 `spec.required`；工具内部已有 `invalid_arg`。
兜底路径上重试只会放大延迟，错误如实回灌让模型下一轮修正。

**4. 结果截断。**
单条工具结果超过 `MAX_RESULT_CHARS = 2000` 截断；原文进 trace，模型只拿头部。

**5. 4 步封顶 + 强制收敛。**
最后一步 `tools=[]` 再调一次，让模型基于已有结果回答；`code=ok/step_limit/error`。

**6. never-raises + 引用去重。**
工具异常/超时都转成 `{"code": ...}` 回灌；`search_kb` 的 chunk id 去重后作为 `citations`。

**7. 工具分发用 `ToolSpec.handler`，不用 if/elif。**
新增工具只需要加一个 `ToolSpec`（含 handler），`execute_tool` 不用改；
静态测试保证每个 spec 都有可调用的 handler。

**8. 参数归一用字段元数据。**
`str_fields` / `upper_fields` / `int_fields` 写在 `ToolSpec` 上，
执行器只调 `_normalize_args(spec, cleaned)`；新增工具不用改统一执行器。

### 验证

- `uv run pytest -q` → **812 passed, 32 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/core/test_react.py` + `tests/core/test_llm_tool_chat.py` → 17 passed

---

## 10 · Task 10 · E 出口接入 ReAct（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/graph/fallback_agent.py` | `needs_tools`、`gate` / `agent` / `scripted` 三节点子图 |
| `tests/graph/test_fallback_agent.py` | 12 条：关键词判定、gate 分流、ReAct 成功/失败、主图接线 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `build.py` · `fallback` 节点 | 直接挂 `fallback_exit` | 挂 `build_fallback_graph()` 子图 | 需要先判断值不值得上 ReAct |
| `state.py` · `ConversationState` | 无 `needs_tools` | 增加该字段 | gate 把判断结果传给条件边 |

### 四个设计点

**1. gate 先判断，不无脑上 ReAct。**
`needs_tools` = 有订单号 / SKU / 数据关键词（订单、物流、快递、到哪、库存、有货、价格、多少钱）。
「你好」「谢谢」直接走脚本，不产生模型调用。

**2. 三节点 / 四转移。**

```
gate ─┬─ needs_tools=false ─► scripted ─► END
      └─ needs_tools=true  ─► agent ─┬─ 有答案 ─► END
                                     └─ 空/失败 ─► scripted ─► END
```

**3. ReAct 成功才用它的答案。**
只有 `code ∈ {ok, step_limit}` 且 `answer` 非空才采纳；
否则清空回答交给 `scripted`，避免把半截或空内容直接展示给用户。

**4. 成功时清空 actions。**
ReAct 回答不承诺转人工，也不带旧动作；失败时由 `fallback_exit` 重新给 `transfer_human`。

### 验证

- `uv run pytest -q` → **824 passed, 32 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/graph/test_fallback_agent.py` + `test_graph_smoke.py` → 37 passed

---

## 11 · Task 11 · 验收（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `docs/reports/ch05-acceptance.md` | 验收清单、六条场景、延迟、发现并修复的问题、已知限制 |

### 验收中发现并修复

| # | 问题 | 修复 |
|---|---|---|
| 1 | 第一轮说原因、第二轮仍问原因 | `chat.py` 新增 `_capture_refund_draft`：问订单号且 draft 缺原因时，轮末补一次抽取 |
| 2 | `validate_retry` 把「继续 / 补单号」也累加，提前转人工 | 槽位命中 / 恢复轮把 `validate_retry` 重置为 0 |
| 3 | ReAct 把 129900 分当成元 | `_with_amount_text` / `_with_price_text` 只回灌 `amount_text` / `price_text` |
| 4 | `try.py chat` 不显示回答 | delta 帧没有 `event`，改成判断 `"delta" in frame` |

### 文档同步

- spec / plan 的演示场景：`1001` → `1002`（1001 是超 7 天考点）；
- 「你们几点上班」→「退货政策是什么」（KB 没有营业时间条目）；
- 串轮污染改为「无金额/订单字段，允许 `resume_flow`」；
- 场景 5 注明：端到端会进 B，只答物流；ReAct 由直接调 E 子图验证。

### 验证

- `uv run pytest -q` → **829 passed, 30 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- 六条场景全部走通；延迟见 `ch05-acceptance.md`
