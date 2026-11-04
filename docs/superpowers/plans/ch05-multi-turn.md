# Ch05 多轮与上下文 实施计划

> **For agentic workers:** 按 task 顺序执行，每个 task 完成即 commit。步骤用 `- [ ]` 追踪。

**Goal:** 让对话跨轮可接续（消息落库 / 窗口 / 指代消解 / 槽位填充 / park / 超时 / 锁），
并把 E 出口接成 **只读 ReAct**（4 个白名单工具、4 步封顶）。

**Architecture:** DB 存全量消息 + 窗口裁剪；跨轮三字段（`active_flow` / `pending_question` / `draft`）
存 `conversations`；槽位命中后由 `classify_intent` 短路回原出口，主图形状不变；
E 出口内部 `gate → agent | scripted`，ReAct harness 自己写循环。

**Tech Stack:** 沿用 Ch01–Ch04（LangGraph / async SQLAlchemy / FastAPI / OpenAI SDK），不引入新依赖。

**Spec:** [ch05-multi-turn-design.md](../specs/ch05-multi-turn-design.md)
**前置:** Ch01–Ch04 已交付；`messages` / `conversations` 三字段在 Ch01 已建好

---

## Global Constraints

- **不新增表、不改 DDL**：Ch01 的 schema 已经够用。
- **主图节点名与边不变**：resume 靠 `classify_intent` 短路，不新增入口边。
- **图里不写 `try/except`**：沿用 Ch04，错误在模块边界转成安全默认值。
- **ReAct 只读**：工具白名单写死在代码里；`create_ticket` / `submit_refund` 永不可见。
- **ReAct 4 步封顶**：最后一步强制不带工具收敛。
- **跨轮只持久化三字段**：`active_flow` / `pending_question` / `draft`，其余状态一轮内有效。
- **同一会话一个 `active_flow`**：新流程替换旧流程，trace 记 `flow_replaced`。
- **每 task 完成后**：追加 `docs/reports/ch05-multi-turn-changes.md`，并同步 spec / plan（若有偏差）。
- 每个 task 完成即 commit。

---

### Task 1: 会话生命周期 + 消息落库 + 锚点（M1）

**Files:**
- Create: `app/core/history.py`、`tests/core/test_history.py`
- Modify: `app/api/chat.py`、`app/main.py`、`scripts/try.py`
- Docs: `docs/reports/ch05-multi-turn-changes.md`（新建）、`docs/reports/00-change-log-overview.md`、`docs/reports/README.md`

**Step 0: 先建改动记录**

按 Ch01–Ch04 的格式建空文件，并在两个索引里登记 Ch05 一行。
**章一开始就建**——最后补会漏掉中间决策。

**Step 1: `app/core/history.py`**

```python
async def save_user_message(
    cid: int, text: str, *, client_message_id: str | None = None
) -> int:
    return await append_message(
        cid, "user", text, client_message_id=client_message_id
    )

async def save_assistant_message(
    cid: int, text: str, *, meta: dict, status: str = "completed"
) -> int:
    return await append_message(cid, "assistant", text, status=status, meta=meta)

async def load_messages(cid: int) -> list[dict]:
    """按写入顺序返回消息，带 `db-{id}` 锚点。"""
```

`load_messages` 每条返回：

```python
{
    "id": f"db-{row.id}",
    "db_id": row.id,
    "role": row.role,
    "content": row.content or "",
    "meta": json.loads(row.meta) if row.meta else None,
}
```

**Step 2: `run_turn` 接会话**

`app/api/chat.py`：

```python
async def run_turn(
    message, conversation_id, trace_id, *,
    client_message_id: str | None = None,
    graph: Any | None = None,
) -> AsyncIterator[dict]:
```

流程：

1. 校验消息：`message` 为空但带了 `selected_action` 时不算空消息；
2. `user_id = get_settings().demo_user_id`（Ch06 换鉴权）；
3. `conversation_id is None` → `create_conversation(user_id)`；
   否则 `get_conversation(conversation_id, user_id=user_id)` 做归属校验；
4. **先落用户消息**，拿 `user_message_id`；
5. 发 `ack`：`{"event": "ack", "conversation_id": cid, "user_message_id": user_message_id}`；
6. 跑图；
7. 落助手消息，`meta={"intent":..., "route":..., "citation_ids":..., "resumed":...}`；
8. `done`：`{"event": "done", "message_id": assistant_message_id}`。

**事务**：助手消息与流程状态（Task 4 的 `save_flow_from_state`）必须共用**同一个 session**，
否则会出现「消息写了、流程没写」的半截状态。两个仓库函数都已支持 `session=` 参数，
`run_turn` 在轮末开一个 `session_scope()` 包住两次写入。

**异常路径**：图执行抛异常时，落一条 `status="error"` 的助手消息（内容用标准话术），
再上抛给 `main.py` 转成 `error` 帧。**不要让用户消息悬空**——下一轮回放会看到没有回复。

**Step 3: `app/main.py`**

`ChatRequest` 增加可选字段：

```python
selected_action: dict | None = None
```

把 `req.client_message_id`、`req.selected_action` 一并传给 `run_turn`。
本轮只透传，Task 6 消费。

**Step 4: `scripts/try.py` 加 `chat` 模式**

```bash
uv run python scripts/try.py chat "我要退款"
```

- 第一次没有 `conversation_id` → 调 `run_turn`，打印 `conversation_id`；
- 同一进程内继续输入 → 复用 `conversation_id`，能手工验证跨轮；
- 打印每帧的 `event` 与关键字段，不打印整段 SSE。

**测试：**
- `save_user_message` / `save_assistant_message` 返回 id，`load_messages` 顺序正确；
- 同一 `client_message_id` 重复调用返回同一个 id；
- `run_turn`（假图）产出 `ack.conversation_id` / `done.message_id`；
- 传入不属于当前用户的 `conversation_id` → `ChatError`（归属校验）。

**验收**：`pytest` 全绿；`try.py chat` 连发两轮，第二轮能取到第一轮的消息。

**Commit:** `feat(history): 会话与消息落库（db-{id} 锚点）`

---

### Task 2: 历史窗口 + 滚动摘要（M2）

**Files:**
- Modify: `app/core/history.py`、`app/core/prompts.py`、`app/db/repo_conversation.py`、`app/api/chat.py`、`app/graph/state.py`
- Test: `tests/core/test_history.py`（追加）

**Step 1: 预算常量**

```python
CONTEXT_TOKENS = 8000
SYSTEM_RESERVE = 1200
EVIDENCE_RESERVE = 2000
SUMMARY_RESERVE = 600
OUTPUT_RESERVE = 800
HISTORY_BUDGET = CONTEXT_TOKENS - SYSTEM_RESERVE - EVIDENCE_RESERVE - SUMMARY_RESERVE - OUTPUT_RESERVE
MAX_TOOL_CHARS = 200
SUMMARY_TIMEOUT = 3.0


def estimate_tokens(text: str) -> int:
    """中文为主，保守按 1 字符 ≈ 1 token；英文会高估，可接受。"""
    return len(text)
```

**Step 2: `Window` 与 `load_window`**

```python
@dataclass(frozen=True)
class Window:
    messages: list[dict]      # 给模型的消息（含锚点）
    summary: str | None
    token_estimate: int
    truncated: bool


async def load_window(cid: int, *, budget: int = HISTORY_BUDGET) -> Window
```

算法：

1. 取全量消息；
2. 若总量超过预算，`maybe_summarize`；
3. 从最新往前取，直到用满预算；
4. **至少保留最近 2 轮**；最新用户消息永远保留（超长截断并标记）；
5. 工具消息超过 `MAX_TOOL_CHARS` 压成一行；
6. 返回 `messages`、`summary`、`truncated`。

**Step 3: 摘要**

- `repo_conversation.set_summary(cid, summary, upto_msg_id)`：只允许 `upto_msg_id` 向前移；
- `maybe_summarize`：取最老的一半未摘要消息 → `chat()` 生成摘要 → 写回；
- 超时 3s；失败保留旧摘要、窗口截断，**不阻断**。

`app/core/prompts.py` 加：

```python
SUMMARY_SYSTEM = """把下面的客服对话压成一段事实摘要。
只保留：用户身份线索、订单号、诉求、已确认的要素、系统承诺。
不写推测，不写客套话。输出纯文本，不超过 150 字。"""
```

**Step 4: state 与 API**

`ConversationState` 增加：

```python
history: list[dict]
summary: str | None
```

`run_turn` 在跑图前：

```python
window = await load_window(cid)
state["history"] = window.messages
state["summary"] = window.summary
```

**测试：**
- 预算内不裁剪；超预算裁最老；最新用户消息必留；
- 工具消息 >200 字被压缩，原文不在窗口里；
- 摘要边界只前移；摘要失败保留旧摘要且不抛；
- `estimate_tokens` 对中文不做 4 字符折算。

**验收**：20 轮假消息下，窗口 token 估算 ≤ 预算；摘要只生成一次（边界稳定）。

**Commit:** `feat(history): 按预算裁窗与滚动摘要`

---

### Task 3: 指代消解（M3）

**Files:**
- Create: `app/core/coref.py`、`tests/core/test_coref.py`
- Modify: `app/core/prompts.py`、`app/graph/nodes.py`
- Test: `tests/graph/test_resolve_reference.py`

**Step 1: prompt 与 schema**

```python
COREF_TOOL_NAME = "emit_resolved_query"
COREF_PARAMETERS = {
    "type": "object",
    "properties": {"resolved_query": {"type": "string"}},
    "required": ["resolved_query"],
}
COREF_SYSTEM = """把用户这句话补成不依赖上文的完整问句。
- 只补指代（那单 / 这个 / 它）和省略，不增删事实
- 订单号、型号、金额原样保留
- 补不出来就原样返回，不要猜
只输出 resolved_query。"""
```

**Step 2: `resolve`**

```python
async def resolve(query: str, history: list[dict]) -> tuple[str, str]:
    """返回 (resolved_query, mode)；mode = rewrite / passthrough / passthrough_error。"""
```

三个短路（不调模型）：

| 条件 | 原因 |
|---|---|
| `history` 为空 | 没有可消解的指代 |
| 原句已含订单号 / SKU | 实体明确，改写只增加风险 |
| 调用方标记 resume | 「1001」这种补槽位不需要改写 |

调用后守卫：

- 结果为空、长度 < 2、或 > 原文 2 倍 → 原句；
- 只有空白差异 → 原句 + `passthrough`；
- 任何异常 → 原句 + `passthrough_error`。

**Step 3: `resolve_reference` 节点**

```python
async def resolve_reference(state):
    raw = state.get("raw_query", "")
    if state.get("resume_route"):
        return {"resolved_query": raw, "resumed": True}
    resolved, mode = await coref.resolve(raw, state.get("history") or [])
    return {"resolved_query": resolved, "resumed": False}
```

- 写一行 `coref` span，attrs：`mode`、`before` / `after` 前 200 字；
- `resolve_reference` 节点本身的 `node:` span 由 API 统一记录，不重复。

**测试：**
- 「那单呢」+ 历史订单 1001 → 改写包含 1001；
- 历史为空 → 不调模型，`passthrough`；
- 原句已含 `订单1001` → 不调模型；
- 模型返回超长 / 异常 → 原句 + 对应 mode。

**Commit:** `feat(coref): 指代消解节点`

---

### Task 4: 跨轮状态 + 30 分钟失效（M4/M7）

**Files:**
- Create: `app/core/session_state.py`、`scripts/cleanup.py`、`tests/core/test_session_state.py`
- Modify: `app/api/chat.py`
- Test: `tests/api/test_chat_sse.py`（追加）

**Step 1: `FlowState`**

```python
FLOW_TTL = timedelta(minutes=30)

@dataclass(frozen=True)
class FlowState:
    active_flow: str | None       # refund / data / None
    pending_question: str | None  # order_id / sku_id / spec / refund_extract
    draft: dict
    expired: bool
```

**Step 2: 读 / 写 / 清**

```python
async def load_flow(cid: int, *, user_id: str) -> FlowState
async def save_flow_from_state(cid: int, state: dict) -> None
async def clear_flow(cid: int) -> None
```

`load_flow`：

1. `get_conversation`（归属校验）；
2. `active_flow` 为空 → 空状态；
3. `now - updated_at > 30min` → `clear_flow` + 空状态 + `expired=True`；
4. 否则解析 `draft` JSON。

`save_flow_from_state`：

| 终态 | 动作 |
|---|---|
| `route ∈ {rules, data}` 且 `pending_question` 非空 | 存三字段 |
| 其余（完成 / 转人工 / 兜底 / 知识） | `clear_flow` |

draft 形状：

```json
{
  "intent": "退款申请",
  "order_id": "1001",
  "validate_retry": 1,
  "refund": {"reason_code": "no_reason", "item_count": 1, "condition_ok": null}
}
```

数据出口用 `{"intent": "订单查询", "order_id": "1001", "sku_id": null}`。

**Step 3: `scripts/cleanup.py`**

```bash
uv run python scripts/cleanup.py
```

查 `active_flow IS NOT NULL AND updated_at < now-30min`，整组清空（走 `clear_flow`）。
打印清理条数；可重复执行。

**Step 4: API 接线**

`run_turn` 在跑图前 `load_flow`；跑完图 `save_flow_from_state`。
**这一步只做持久化，不做接回**——接回在 Task 6。

**测试：**
- 30 分钟内保留；31 分钟清空并 `expired=True`；
- 完成 / 转人工时清空；
- draft 形状正确；`validate_retry` 被保存；
- 清理脚本只清过期的。

**Commit:** `feat(state): 跨轮流程状态与 30 分钟失效`

---

### Task 5: 槽位填充（M6）

**Files:**
- Create: `app/core/slot_fill.py`、`tests/core/test_slot_fill.py`

**Step 1: `SlotResult`**

```python
@dataclass(frozen=True)
class SlotResult:
    filled: bool
    slot: str | None
    value: Any = None
    cancel: bool = False
    resume: bool = False


async def fill(
    pending_question: str | None,
    text: str,
    *,
    draft: dict,
    selected: dict | None = None,
) -> SlotResult
```

**Step 2: 判定顺序**

```
取消词 → 恢复词 → 按 pending_question 抽槽位 → 都不是（未命中）
```

| 词表 | 内容 |
|---|---|
| 取消 | 取消 / 算了 / 不退了 / 不用了 / 先这样 |
| 恢复 | 继续 / 接着 / 回到退款 / 继续退款 / 好的继续 |

**Step 3: 按槽位抽取**

| `pending_question` | 方式 |
|---|---|
| `order_id` | 全角数字归一 + `params.extract_order_id` |
| `sku_id` | `params.extract_sku`（转大写） |
| `spec` | **透传**：非空即 `filled`，匹配交给 `data_exit.query` |
| `refund_extract` | `structured_call(EXTRACT_TOOL_NAME, EXTRACT_PARAMETERS, extract_messages(text))` |

退款字段合并：只取 `_clean_draft` 认可的字段；新值缺失**不覆盖**旧值。
`condition_ok=false` 是有效值，必须能覆盖旧的 `None`。

**Step 4: 边界**

- `pending_question` 为空 → `filled=False`，不调模型；
- 用户问句（含「吗 / ？/ 几点」）不当作订单号；
- 抽取异常 → `filled=False`，不抛。

**测试：**
- 「1001」→ `order_id`；全角「１００１」也能抽；
- 「st-20」→ `ST-20`；
- 「星光灰」+ `pending_question=spec` → 透传；
- 「算了」→ `cancel`；「继续退费」→ `resume`；
- `selected={"type":"select_order","order_id":"1001"}` → 直接填 `order_id`；
- 点选类型与 `pending_question` 不匹配 → 忽略，不填错槽；
- 退款补一句「就一件」→ 只合并 `item_count`，旧 `reason_code` 不丢；
- 模型格式错误 → `filled=False`。

**Commit:** `feat(slot): 追问槽位填充`

---

### Task 6: resume 路由（M6）

**Files:**
- Modify: `app/graph/state.py`、`app/graph/nodes.py`、`app/graph/rules_exit.py`、`app/api/chat.py`
- Test: `tests/graph/test_resume.py`、`tests/api/test_chat_sse.py`（追加）

**Step 1: state 字段**

```python
resume_route: str | None   # rules / data / knowledge / human / fallback
resumed: bool
skip_extract: bool          # 恢复轮只补了订单号，不需要重新抽取
```

**Step 2: `classify_intent` 短路**

先在 `app/graph/routing.py` 加一个单一来源：

```python
ROUTE_NAMES = ("knowledge", "data", "rules", "human", "fallback")
```

`build.py` 的 `EXIT_NODES` 改为 `ROUTE_NAMES`，避免两处各写一份。

```python
resume = state.get("resume_route")
if resume in ROUTE_NAMES:
    return {
        "intent": state.get("intent"),
        "intent_confidence": 1.0,
        "route": resume,
        "resumed": True,
    }
```

**不调模型、不写 `multi_intent`。** `nodes.py` 从 `routing` 导入，不 import `build.py`，
避免循环依赖。

**Step 3: API 编排**

`run_turn` 在 `load_flow` 之后：

```python
slot = await slot_fill.fill(
    flow.pending_question, text, draft=flow.draft, selected=selected_action
)
if slot.cancel:
    await clear_flow(cid)
elif slot.resume or slot.filled:
    intent = flow.draft.get("intent")
    if intent in INTENT_TO_ROUTE:          # draft 损坏时不硬接
        state["resume_route"] = INTENT_TO_ROUTE[intent]
        state["intent"] = intent
        state["order_id"] = flow.draft.get("order_id")
        state["sku_id"] = flow.draft.get("sku_id")
        state["refund_draft"] = flow.draft.get("refund") or {}
        state["validate_retry"] = flow.draft.get("validate_retry", 0)
```

- 命中后**不重新分类**；
- `intake` 若从「1001」再抽一次也无害（同值）；
- `refund_draft` 已有值时，`extract` 会合并而非覆盖（现有实现已满足）；
- `selected_action` 映射：`select_order` → `order_id`、`select_sku` → `sku_id`、
  `select_spec` → `spec`；类型不匹配则忽略并记 trace；
- **这不是 `Command(resume=...)`**：图仍然从入口跑，只是 `classify_intent` 短路。
  真正的 `interrupt` 恢复在 P5-12。

**Step 4: 恢复轮跳过重复抽取**

用户只是补订单号时，C 出口的 `extract` 没必要再调一次模型。

```python
# API 层：只补了订单号（点选，或原话去掉订单号后为空）
state["skip_extract"] = (
    state.get("resume_route") == "rules"
    and slot.slot == "order_id"
    and (not text.strip() or text.replace(state.get("order_id") or "", "").strip(" ，,。.") == "")
)
```

`rules_exit.extract` 开头：

```python
if state.get("skip_extract"):
    return {"refund_draft": dict(state.get("refund_draft") or {})}
```

**不能一刀切跳过**：用户说「1001，尺寸不合适」时仍要抽取并合并。
只有「纯补单号」才跳过。

**Step 5: 验收两条**

1. 「我要退款，尺寸不合适」→「1001」：第二轮直接判定，原因还在；
2. 「我要查订单」→「1001」：第二轮直接查订单。

**测试：**
- resume 轮 `classify` 不被调用（monkeypatch 断言）；
- 纯补单号时 `extract` 不调模型，draft 原样保留；
- 「1001，尺寸不合适」仍调抽取并合并；
- `refund_draft` / `order_id` / `validate_retry` 被恢复；
- 主图节点名与边不变（现有结构测试继续通过）。

**Commit:** `feat(graph): 槽位命中后接回原出口`

---

### Task 7: park（M5）

**Files:**
- Modify: `app/core/session_state.py`、`app/api/chat.py`
- Test: `tests/graph/test_park.py`

**Step 1: 判定**

```python
parked = (
    flow.active_flow is not None
    and not flow.expired
    and not slot.cancel
    and not slot.resume
    and not slot.filled
)
```

**Step 2: 本轮结束后**

| 情况 | 动作 |
|---|---|
| 新话题完成，且没有新的 `pending_question` | **保留旧流程**；回答尾部加提示 + `resume_flow` 动作 |
| 新话题又产生 `pending_question` | 新流程替换旧流程；trace 记 `flow_replaced` |
| 新路由是 `human` | 清空旧流程，不加提示 |

提示文案：

```
（您还有一笔未完成的退款申请，需要继续吗？）
```

动作：

```python
{"type": "resume_flow", "flow": "refund"}
```

**只加一次**：本轮加了就结束；下一轮不重复。

**Step 3: 恢复**

用户下一轮说「继续退费」→ `slot_fill` 返回 `resume=True` → Task 6 的接回路径。

**测试：**
- 退款挂起 → 问营业时间 → 正常答 + 提示 → 「继续退费」→ 回到退款；
- 挂起时问另一个会追问的意图 → 新流程替换（trace `flow_replaced`）；
- 挂起时转人工 → 旧流程清空、无提示；
- 第 2 轮闲聊的回答里**没有退款金额/字段**。

**Commit:** `feat(park): 改话题保留未完成流程`

---

### Task 8: 会话级锁（M8）

**Files:**
- Create: `app/core/lock.py`、`tests/core/test_lock.py`
- Modify: `app/api/chat.py`
- Test: `tests/api/test_chat_sse.py`（追加）

**Step 1: `conversation_lock`**

```python
class BusyError(Exception):
    """同一会话已有请求在处理，等待超时。"""


@asynccontextmanager
async def conversation_lock(cid: int, *, timeout: float = 5.0):
    ...
```

- 字典 `{cid: (lock, refcount)}`，外层一个 `asyncio.Lock` 保护字典；
- `wait_for(lock.acquire(), timeout)`；超时抛 `BusyError`；
- `finally` 释放并减引用，归零时移除；
- **单进程有效**，写进注释与已知限制。

**Step 2: `run_turn` 包锁**

```python
async with conversation_lock(cid):
    async for frame in _run_turn_locked(...):
        yield frame
```

锁必须覆盖整个流式输出；客户端断开时生成器关闭，`finally` 释放。

**Step 3: 错误映射**

`run_turn` 捕获 `BusyError` → `raise ChatError("busy", "上一条消息还在处理中，请稍后再试")`。
`main.py` 已有的 `ChatError` 分支会把它转成 `error` 帧。

**测试：**
- 两个并发任务同 cid：最大并发 1；
- 第一个持锁 > 5s：第二个抛 `BusyError`；
- 不同 cid 不互斥。

**Commit:** `feat(lock): 会话级串行执行`

---

### Task 9: 只读 ReAct harness（P5-9a）

**Files:**
- Create: `app/core/react.py`、`tests/core/test_react.py`
- Modify: `app/core/llm.py`

**Step 1: `llm.tool_chat`**

```python
async def tool_chat(
    messages: list[dict], tools: list[dict], *, temperature: float = 0.2
) -> Any:
    """多轮工具调用的一步；返回 assistant message（含 tool_calls 或 content）。
    不重试：工具循环的下一步会自然纠偏；网络/鉴权错误照常上抛。"""
```

**Step 2: 工具 schema**

```python
MAX_STEPS = 4
TOOL_WHITELIST = ("query_order", "query_logistics", "query_stock", "search_kb")

TOOLS = [
    {"type": "function", "function": {"name": "query_order", ...}},
    {"type": "function", "function": {"name": "query_logistics", ...}},  # 参数是 order_id
    {"type": "function", "function": {"name": "query_stock", ...}},
    {"type": "function", "function": {"name": "search_kb", ...}},
]
```

**Step 3: 执行器**

```python
async def _execute(name: str, args: dict, *, user_id: str) -> dict
```

| 工具 | 实现 |
|---|---|
| `query_order` | `tools.orders.query_order(order_id, user_id=user_id)` |
| `query_logistics` | 先 `query_order` 取 `tracking_no`，再 `query_logistics(tracking_no, user_id=...)` |
| `query_stock` | `tools.orders.query_stock(sku_id, spec)` |
| `search_kb` | `retrieval.search(query, top_k=3)` |

- `args` 里的 `user_id` 一律丢弃（schema 里也没有）；
- 工具 `ToolResult` → `{"ok":..., "code":..., "data":...}`；
- 任何异常 → `{"code": "internal"}`，**不抛**。

**Step 4: 循环**

```python
async def run(query, *, history, user_id, trace_id) -> ReactResult
```

```
messages = [system] + history + [user]
for step in range(1, MAX_STEPS + 1):
    resp = await tool_chat(messages, TOOLS if step < MAX_STEPS else [])
    if not resp.tool_calls:
        return answer
    执行每个 tool_call → 追加 tool message
step 用尽：不带工具再调一次，强制回答
```

- 每步一行 `react:step:N` span；父 span `react:total`；
- `code`：`ok` / `step_limit` / `error`。

**测试：**
- 无工具直接回答（1 步）；
- 一次工具调用后回答（2 步）；
- 4 步仍未收敛 → 第 5 次不带工具，`code=step_limit`；
- 工具抛异常 → 回灌错误，不中断循环；
- 模型传 `user_id` → 执行器丢弃；
- `create_ticket` / `submit_refund` 不在 `TOOL_WHITELIST`（静态断言）。

**Commit:** `feat(react): 只读 ReAct harness`

---

### Task 10: E 出口接入（P5-9b）

**Files:**
- Create: `app/graph/fallback_agent.py`、`tests/graph/test_fallback_agent.py`
- Modify: `app/graph/build.py`
- Test: `tests/graph/test_graph_smoke.py`（追加）

**Step 1: `gate`**

```python
def needs_tools(text: str) -> bool:
    return bool(
        extract_order_id(text)
        or extract_sku(text)
        or any(k in text for k in DATA_KEYWORDS)
    )
```

`DATA_KEYWORDS = ("订单", "物流", "快递", "到哪", "库存", "有货", "价格", "多少钱")`。

**Step 2: 子图**

```
gate ─┬─ False ─► scripted ─► END
      └─ True  ─► agent ─┬─ 有答案 ─► END
                          └─ 无答案/异常 ─► scripted ─► END
```

- `scripted` 直接调用 `nodes.fallback_exit`，不复制三脚本；
- `agent` 调 `react.run(...)`；`code in {"ok","step_limit"}` 且 answer 非空 → 用答案；
- 否则走 `scripted`。

**Step 3: 接线**

```python
g.add_node("fallback", build_fallback_graph())
```

主图节点名与边不变；`fallback_exit` 仍被 `scripted` 使用。

**测试：**
- 「你好」→ `gate=False`，不调 ReAct；
- 「订单1001 到哪了」→ `gate=True`，调 `react.run`（打桩）；
- ReAct 返回空 → 脚本兜底；
- 主图 `fallback` 是子图；5 出口路由不变；
- 静态：`fallback_agent.py` / `react.py` 不 import 写工具。

**Commit:** `feat(fallback): E 出口接入只读 ReAct`

---

### Task 11: 验收

**Files:**
- Create: `docs/reports/ch05-acceptance.md`
- Modify: `docs/superpowers/plans/00-tasks.md`（P5）、`docs/reports/ch05-multi-turn-changes.md`

**Step 1: 六条演示场景**

用 `scripts/try.py chat` 跑 spec 的验收场景：

1. 退款 →「1001」；
2. 退款 →「你们几点上班」→「继续退费」→「1001」；
3. 退款 →「你好」；
4. 「订单1001 到哪了」→「那单呢」；
5. 「订单1001 的物流到哪了，顺便看下 ST-20 还有货吗」；
6. 「你好」。

**Step 2: 延迟（n=5）**

| 场景 | 目标 |
|---|---|
| 普通多轮 | P95 ≤ 6s |
| ReAct 兜底 | P95 ≤ 15s |
| 摘要触发轮 | 额外 ≤ 3s |

记录 min / median / max，写进验收报告。

**Step 3: 验收清单**

- [ ] `pytest` 全绿
- [ ] 跨轮退款接续，原因不丢
- [ ] park 后能恢复
- [ ] 串轮无污染
- [ ] 指代消解成功 / 不明时追问
- [ ] 消息锚点与 DB id 一致
- [ ] 30 分钟失效
- [ ] 并发串行 + `busy`
- [ ] ReAct 只读 + 4 步封顶
- [ ] 每步 `react:step:N` span
- [ ] 更新 P5 勾选
- [ ] 写 `ch05-acceptance.md`

**Step 4: 同步文档**

- `00-tasks.md` P5-1 … P5-9 标 ✅；P5-10/11/12 标 ⏳ 并写明触发条件；
- `docs/reports/ch05-multi-turn-changes.md` 补最后一节；
- 若实现与 spec 有偏差，**两份都改**，不只改代码。

**Commit:** `test: Ch05 验收（多轮接续 + park + ReAct）`

---

## 与 spec 的对应

| spec 验收项 | 落在哪个 Task |
|---|---|
| 跨轮退款接续 | Task 6 / 11 |
| park 再回来 | Task 7 / 11 |
| 串轮污染 | Task 4 / 7 / 11 |
| 指代消解 | Task 3 / 11 |
| 窗口与摘要 | Task 2 |
| 消息锚点 | Task 1 / 2 |
| 30 分钟失效 | Task 4 |
| 会话锁 | Task 8 |
| ReAct 长尾作答 | Task 9 / 10 |
| 只读 + 4 步封顶 | Task 9 / 10 |
| 每步 trace | Task 3 / 9 |
| 主图形状不变 | Task 6 / 10 |

## 风险

| 风险 | 应对 |
|---|---|
| park 与「新流程自己也要追问」冲突 | 单 `active_flow`，新流程替换旧流程并记 `flow_replaced`（Task 7） |
| 摘要调用拖慢普通轮次 | 只在超预算时触发；3s 超时；失败不阻断 |
| 指代消解改错实体 | 保留 `raw_query`；长度守卫；trace 存前后文 |
| resume 后 `extract` 覆盖旧字段 | `extract` 已是「合并而非覆盖」；`_clean_draft` 丢掉非法值，不写 None |
| 锁只在本进程有效 | 注释 + 已知限制；多 worker 前换分布式锁 |
| ReAct 编造 / 超时 | 只读工具、错误回灌、4 步封顶、失败退脚本 |
| 测试碰真实库 | 沿用 `session` 夹具；ReAct / 检索在测试里打桩 |
| 消息落库失败影响回答 | 先保证流式回答；落库异常记日志，不替换回答 |
