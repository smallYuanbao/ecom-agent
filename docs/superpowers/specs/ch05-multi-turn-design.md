# Ch05 设计：多轮与上下文 + E 出口只读 ReAct

> 依据：[多轮与上下文设计](../../design/电商客服-多轮与上下文设计.md) M1–M8、
> [意图与出口设计](../../design/电商客服-意图与出口设计.md) §二/§六、
> 技术决策 D5（恢复）/ D6（状态内容）/ D7（身份注入）/ D9（错误码）、
> 参考实现 `resolve_reference` / `main_agent` / `agent_tools`。
> 范围：**消息持久化与窗口 / 指代消解 / 跨轮状态 / 槽位填充 / park / 超时与锁 /
> E 出口只读 ReAct**。

---

## 目标

1. 一轮对话的消息**落库可回放**：`messages` 表存全量，送模型时按预算裁剪
2. 用户离开再回来**能接上**：退费问到一半去问营业时间，回来继续说订单号能续
3. **不重跑已完成的判定**：槽位填充命中后直接回到原出口，不重新分类
4. `pending_question` / `draft` / `active_flow` **跨请求存活**，30 分钟后自动失效
5. 同一会话并发两条消息**串行执行**，不互相覆盖状态
6. 指代消解把「那单」「这个」补成完整问句，且**失败不阻断**
7. **E 出口接入只读 ReAct**：兜底长尾能用 4 个白名单工具作答，4 步封顶
8. 写操作**一律不进 ReAct 工具集**；模型不能通过参数伪造 `user_id`
9. 主图**节点名与边不变**，前四章的结构测试继续有效

### 验收标准

1. 「我要退款，尺寸不合适」→ 问订单号 → 「1002」→ 继续判定，**不再问原因**
2. 退费问到一半问「退货政策是什么」→ 正常回答 → 「继续退费」→ 回到原流程
3. 第 1 轮退款、第 2 轮闲聊：第 2 轮**没有退款金额/订单字段**；
   park 允许带一个 `resume_flow` 提示动作
4. 「订单1001 到哪了」→「那单呢」→ 能接回 1001；指代不明时**追问，不猜**
5. 20 轮以上对话：窗口不超预算，早期订单号仍能被引用（摘要或原文）
6. 消息落库后重新构建窗口，`db-{id}` 锚点与 DB 主键一一对应
7. 把 `updated_at` 改成 31 分钟前：下一轮流程字段被清空
8. 同一会话并发两条：串行执行；等待超 5s 返回 `busy`
9. 兜底长尾「订单1001 的物流到哪了，顺便看下 ST-20 还有货吗」：ReAct 调工具作答，≤4 步
10. 「你好」不触发 ReAct；工具集里没有 `create_ticket` / `submit_refund`
11. 步数用尽仍未收敛：退回兜底脚本，不无限循环
12. 每步一行 trace（`react:step:N`），工具参数与结果可还原

---

## 技术栈

沿用 Ch01–Ch04：LangGraph、async SQLAlchemy、FastAPI、OpenAI SDK。**本章不引入新依赖。**
不引入 LangChain 的 AgentExecutor：ReAct 循环自己写，才能控制步数、白名单、身份注入与 trace。

---

## 背景：为什么单独拆一维

前四章把「单轮怎么处理」做完了，但退费天然跨轮：

```
用户：我要退款，尺寸不合适
系统：请提供订单号          ← 本轮结束，状态必须活到下一轮
用户：1001                 ← 这句话怎么接回流程？
系统：核对到订单…
用户：等等，你们几点上班？   ← 中途改话题，进度不能丢
```

这三步任何一步处理不好，用户都要重来。本章补的就是**轮与轮之间的状态**。

---

## 架构

### 一轮的生命周期

```
POST /api/chat
  │
  ├─ 1. 校验消息（空/超长）
  ├─ 2. 取或建会话（归属校验；Ch06 换真实鉴权）
  ├─ 3. 会话级锁（M8，5s 超时 → busy）
  ├─ 4. 加载跨轮状态（M4）+ 超时检查（M7）
  ├─ 5. 槽位填充（M6）
  │      ├─ 取消词 → 清空流程，正常回话
  │      ├─ 命中槽位 / 恢复词 → 写 resume_route，接回原出口
  │      └─ 未命中 → 记下「有新话题」，继续第 6 步
  ├─ 6. 构建历史窗口（M2，必要时摘要；**不含本轮用户消息**）
  ├─ 7. 用户消息落库（M1，幂等键）
  ├─ 8. 跑图（resolve_reference → classify_intent → 出口）
  ├─ 9. 助手消息落库 + 流程状态落库/清空（同事务）
  └─ 10. SSE 帧输出
```

**为什么槽位填充在分类之前**：`1001` 这种回答没有意图可言。
先按「当前在等什么」抽值，抽到就直接接回原流程；抽不到才当新话题分类。
把「补充信息」塞进意图分类会污染意图体系（设计 M6）。

### 主图变化

```
resolve_reference ─► classify_intent ─► 五个出口
```

**节点与边一个都不改**，只换实现：

| 节点 | 变化 |
|---|---|
| `resolve_reference` | 透传 → 调 `coref.resolve()`；resume 轮直接透传 |
| `classify_intent` | 有 `resume_route` 时**短路**：不调模型，直接用恢复的路由 |
| `fallback` | 节点函数 → `build_fallback_graph()` 编译后的子图 |

**为什么 resume 不新增入口边**：新增分支会让主图形状随场景变化，
前四章「5 出口 → END」的结构测试要跟着改。把短路放进 `classify_intent`，
图的形状稳定，行为仍可单测。

### E 出口子图

```
gate ─┬─ needs_tools=false ─► scripted ─► END
      └─ needs_tools=true  ─► agent ─┬─ 收敛 ─► END
                                     └─ 失败/超步 ─► scripted ─► END
```

| 节点 | 职责 |
|---|---|
| `gate` | 判断这句话值不值得上 ReAct；闲聊直接走脚本 |
| `agent` | 只读 ReAct：模型选工具 → 执行 → 回灌 → 再判断，最多 4 步 |
| `scripted` | 复用 Ch02 的三个兜底脚本（问候 / 致谢 / 超范围） |

**`gate` 为什么必要**：如果兜底一律进 ReAct，「你好」也要一次模型调用 + 最多 4 步。
ReAct 是**升级路径**，不是兜底入口。

`needs_tools = 订单号正则命中 OR SKU 正则命中 OR 数据关键词命中`
（订单 / 物流 / 快递 / 到哪 / 库存 / 有货 / 价格 / 多少钱）。

---

## 模块与契约

### `app/core/history.py` · 消息与窗口（M1/M2）

```python
@dataclass(frozen=True)
class Window:
    messages: list[dict]      # [{"role": "user", "content": ..., "id": "db-1"}]
    summary: str | None
    token_estimate: int
    truncated: bool


async def load_window(cid: int, *, budget: int = HISTORY_BUDGET) -> Window
async def save_user_message(cid: int, text: str, *, client_message_id: str | None) -> int
async def save_assistant_message(cid: int, text: str, *, meta: dict, status: str = "completed") -> int
async def maybe_summarize(cid: int, window: Window) -> Window
```

**锚点约定**：每条消息带 `id=f"db-{row.id}"`，与 DB 主键一致。
摘要边界 `summary_upto_msg_id` 存的是 **DB id**，不是「第几条」。

**预算按块分配**（数值可调，公式固定）：

```
自设上下文上限        8000 tokens
 ├─ 系统提示 + 工具    1200
 ├─ 检索证据预留       2000
 ├─ 摘要段预留          600
 ├─ 输出预留            800
 └─ 历史窗口预算       3400
```

**估算口径**：中文为主，保守按 **1 字符 ≈ 1 token**；
英文会高估，可接受。不能按 4 字符/token 估，否则低估约 4 倍。

**裁窗规则**：

1. 从最新消息往前取，直到用满预算；
2. 至少保留最近 2 轮原文，不为省 token 把当前上下文裁没；
3. 最新一条用户消息永远保留（超长则截断内容并标记）；
4. 被裁掉且 `id <= summary_upto_msg_id` 的部分由摘要代表；边界**只向前移**。

**摘要触发**：未压缩历史超过窗口预算 → 取最老的一半压成一段摘要，
边界一次挪到位。同步调用、超时 3s；失败保留旧摘要并截断窗口，
**不阻断本轮**（摘要失败不该让用户发不出消息）。

**工具结果**：同一轮内原样进上下文；落库时只存**一行摘要**（≤200 字），
原始返回进 trace。这样历史不会被一次订单 JSON 吃光，同时审计仍能看到原始值。

### `app/core/session_state.py` · 跨轮状态（M4/M7）

```python
@dataclass(frozen=True)
class FlowState:
    active_flow: str | None       # refund / data / None
    pending_question: str | None  # order_id / sku_id / spec / refund_extract
    draft: dict
    expired: bool


async def load_flow(cid: int, *, user_id: str) -> FlowState
async def save_flow_from_state(cid: int, state: dict) -> None
async def clear_flow(cid: int) -> None
```

**写回规则（turn 结束时）**：

| 终态 | 动作 |
|---|---|
| `route ∈ {rules, data}` 且 `pending_question` 非空 | 存 `active_flow` + `pending_question` + `draft` |
| 流程完成 / 转人工 / 兜底 / 知识出口 | `clear_flow` |
| 用户说「取消 / 算了」 | `clear_flow` |

**draft 至少包含 `intent`**：恢复时要靠它知道回哪个出口、哪个数据意图。
退款再合并 `refund_draft`、`order_id`、`validate_retry`。统一形状：

```json
{
  "intent": "退款申请",
  "order_id": "1001",
  "validate_retry": 1,
  "refund": {"reason_code": "no_reason", "item_count": 1, "condition_ok": null}
}
```

**为什么把退款字段嵌在 `refund` 下**：外层是流程元信息，内层是 C 出口的
`refund_draft`。恢复时直接 `state["refund_draft"] = draft["refund"]`，
不会把 `intent` / `validate_retry` 混进判定 prompt。

**同一会话只保留一个 `active_flow`**：如果用户在新话题上又触发了一个追问
（例如退款挂起时问「订单 1001 到哪了」且没给单号），新流程会替换旧流程，
trace 记 `flow_replaced`。换流程比"两个流程抢同一个槽位"安全——
后者会让下一句 `1001` 被错误地喂给退款流程。

**超时**：`FLOW_TTL = 30 分钟`，以 `conversations.updated_at` 为准。
加载时惰性判断：过期就清空并返回空状态；另配 `scripts/cleanup.py` 批量清理。

### `app/core/slot_fill.py` · 槽位填充（M6）

```python
@dataclass(frozen=True)
class SlotResult:
    filled: bool
    slot: str | None
    value: Any
    cancel: bool = False
    resume: bool = False


async def fill(
    pending_question: str | None,
    text: str,
    *,
    draft: dict,
    selected: dict | None = None,
    session=None,
) -> SlotResult
```

| `pending_question` | 抽取方式 | 命中条件 |
|---|---|---|
| `order_id` | 正则（含全角数字归一） | 出现 4 位以上数字 |
| `sku_id` | 正则 + 转大写 | 匹配 `[A-Za-z]{2}-\d{2}` |
| `spec` | **透传** | 非空且不是取消词；规格匹配交给 `query` 节点 |
| `refund_extract` | 复用 C 出口的抽取 prompt | 新抽出的字段非 None |

**取消词**：取消 / 算了 / 不退了 / 不用了 / 先这样。
**恢复词**：继续 / 接着 / 回到退款 / 继续退款 / 好的继续。
**判定顺序**：取消 → 恢复 → **结构化点选** → 抽槽位 → 都不是则按新话题处理。

### 选单卡片怎么回来（重要）

Ch04 的 `actions.type=select_order` 只是**给前端渲染卡片的载荷**：
图在 `need_info` 写完后已经走到 `END`，**没有挂起，也没有 `interrupt`**。

用户点选后，前端发一条**新的 `/api/chat` 请求**，两种方式任选：

```json
// A. 当普通消息发（零后端改动）
{"message": "1001", "conversation_id": 12}

// B. 带结构化点选（更可靠，前端不用拼文案）
{"message": "", "conversation_id": 12,
 "selected_action": {"type": "select_order", "order_id": "1001"}}
```

`selected_action` 与 `pending_question` 匹配时直接填槽（例如
`select_order` → `order_id`、`select_sku` → `sku_id`、`select_spec` → `spec`），
不匹配则忽略并记 trace。

**这不是 LangGraph 的 `Command(resume=...)`**。真正的 `resume` 要求
`interrupt()` + checkpointer（P5-12）。本章的「接回」是：
新一轮请求 → 槽位填充 → `resume_route` 短路。**图从来不会停在原地**。

**命名说明**：本章 `resume_route` / `resume_flow` 里的 "resume" 指
「接回未完成流程」，不是 LangGraph 的 `Command(resume=...)`。两者不要混。

**恢复轮不重复抽取**：如果用户只是补一个 `order_id`（结构化点选，或原话去掉订单号后为空），
C 出口的 `extract` 直接沿用已恢复的 `refund_draft`，**不再调模型**。
如果用户同时补充了新信息（「1001，尺寸不合适」），仍正常抽取并合并。

这样「选单后继续」在用户侧是连续的，在成本上也只多一次正则 + 一次 DB 查询；
真正需要「图内断点续跑」时再上 P5-12。

**规格为什么透传**：规格是开放集合，`slot_fill` 不查库也能让
`data_exit.query` 用数据库行做最长匹配——匹配不到会在出口内继续追问。

**退款其他槽位**：只对单句调用现有抽取器，**合并而非覆盖**：
新值为 `None` 表示「这句没说」，保留旧值。
`condition_ok` 的三态语义不变（`None` 不等于 `False`）。

### `app/core/coref.py` · 指代消解（M3）

```python
async def resolve(query: str, history: list[dict]) -> tuple[str, str]
# 返回 (resolved_query, mode)；mode = rewrite / passthrough / passthrough_error
```

**M3 决定每轮都做**，但加三个零成本短路：

| 短路 | 原因 |
|---|---|
| 本轮是 resume（已由槽位填充接回） | 「1001」不需要改写 |
| 历史为空 | 没有可消解的指代 |
| 原句已含订单号 / SKU | 实体已经明确，改写只会引入风险 |

其余每轮一次调用。Prompt 约束：**只补指代与省略，不增删事实**；
订单号、SKU、金额原样保留。

**结果守卫**：改写后长度 > 原文 2 倍、或 < 2 字符、或只有空白差异 → 用原句。
**失败降级**：任何异常 → 原句 + `passthrough_error`，不阻断。
**留痕**：span 里存改写前后各前 200 字，`mode` 进 attrs。
**单一来源**：意图、检索、规则判定都读 `resolved_query`，下游不许再改写。

### `app/core/lock.py` · 会话级锁（M8）

```python
@asynccontextmanager
async def conversation_lock(cid: int, *, timeout: float = 5.0): ...
```

- 锁粒度 = `conversation_id`；同一会话串行，不同会话互不影响；
- 等待超 5s 抛 `BusyError` → API 层映射成 `busy` 错误帧；
- 引用计数：最后一个使用者释放时从字典移除，避免锁表无限增长；
- **单进程有效**。多 worker / 多实例要换 Redis 或 DB advisory lock，写进已知限制。

### `app/core/react.py` · 只读 ReAct harness（P5-9）

```python
MAX_STEPS = 4
TOOL_WHITELIST = ("query_order", "query_logistics", "query_stock", "search_kb")

@dataclass(frozen=True)
class ReactResult:
    answer: str
    steps: int
    tool_calls: list[dict]
    citations: list[str]
    code: str | None      # ok / step_limit / error


async def run(query: str, *, history: list[dict], user_id: str) -> ReactResult
```

**循环**：

```
step = 0
while step < MAX_STEPS:
    调模型（带 4 个工具 schema）
    ├─ 无 tool_calls → 得到最终回答，结束
    └─ 有 tool_calls → 逐个执行 → 回灌 ToolMessage → step += 1
step == MAX_STEPS → 不带工具再调一次，强制基于已有结果回答
```

**四个工具**：

| 工具 | 参数 | 实现 |
|---|---|---|
| `query_order` | `order_id` | `app/tools/orders.query_order` |
| `query_logistics` | `order_id` | 先 `query_order` 取 `tracking_no`，再查轨迹；封装成一步 |
| `query_stock` | `sku_id`, `spec?` | `app/tools/orders.query_stock` |
| `search_kb` | `query`, `top_k=3` | `app/core/retrieval` |

**为什么物流工具收 `order_id` 而不是 `tracking_no`**：两步依赖是固定的，
不该让模型自己想到「先取单号」。能确定的链就写进工具，不当作探索题。

**三条硬约束**：

1. **只读**：白名单在代码里写死；`create_ticket` / `submit_refund` 不在其中，
   有静态测试断言；
2. **身份注入**：`user_id` 由执行器传给工具，工具 schema 里没有这个参数；
   模型多传的字段一律丢弃并记日志；
3. **永不抛异常**：工具错误转成 `{"code": ..., "message": ...}` 回灌给模型；
   模型/网络异常 → `ReactResult(code="error")`，由 `agent` 节点退到脚本。

**trace**：每步一行 `react:step:{N}`，attrs 含工具名、错误码、耗时、
结果前 500 字；父 span `react:total` 记总步数与是否收敛。

### `app/graph/fallback_agent.py` · E 出口子图

```python
def build_fallback_graph() -> CompiledStateGraph
```

3 节点 / 4 转移：`gate → agent | scripted`，`agent → END | scripted`。
`scripted` 直接复用 `nodes.fallback_exit` 的三脚本，不复制话术。

---

## 状态字段（新增）

| 字段 | 写入者 | 用途 |
|---|---|---|
| `history` | API 层 | 窗口消息（含 `db-{id}` 锚点），供 `resolve_reference` 使用 |
| `summary` | API 层 | 滚动摘要；无摘要时为 `None` |
| `resume_route` | API 层 | 槽位填充命中后要回的出口；空 = 正常分类 |
| `resumed` | `classify_intent` | 本轮是否恢复流程；进 trace / SSE |
| `multi_intent` | `classify_intent` | 分类已有该信号；**本章仍不消费**（P5-11 延后） |

**跨轮只持久化三个字段**（`active_flow` / `pending_question` / `draft`）。
其余状态字段都是一轮内的中间产物，不落库——避免「两份状态互相打架」。

---

## 数据模型

**本章不新增表、不改 DDL。** Ch01 已建好：

| 表 / 字段 | Ch05 的用法 |
|---|---|
| `messages` | 全量消息；`client_message_id` 部分唯一索引做幂等 |
| `messages.meta` | 存 `intent` / `route` / `citation_ids`，回放时还原 |
| `conversations.summary` / `summary_upto_msg_id` | 滚动摘要与边界 |
| `conversations.active_flow` / `pending_question` / `draft` | 跨轮三字段 |
| `conversations.updated_at` | 30 分钟超时基准 |
| `idx_msg_conv` | 按会话顺序取消息 |

**这是「数据地基先行」的回报**：Ch05 只写代码，不动 schema。

---

## 接口契约（SSE）

请求新增可选字段 `selected_action`（结构化点选，见「选单卡片怎么回来」）。
`message` 与 `selected_action` 至少给一个；只给后者时，`message` 可为空串。

帧顺序不变：`ack → route → delta → citations → actions → done`。

| 帧 | 变化 |
|---|---|
| `ack` | 增加 `conversation_id`、`user_message_id`（前端可持久化会话） |
| `route` | 增加 `resumed: bool`（本轮是否接回流程） |
| `done` | 增加 `message_id`（助手消息 id） |
| `actions` | 新增 `{"type": "resume_flow", "flow": "refund"}` |
| `error` | 新增 `busy`（会话锁等待超时） |

**流的生命周期 = 锁的生命周期**：锁在进入 `run_turn` 时获取，
在生成器 `finally` 中释放；客户端断开也会释放。

---

## 关键决策记录

| # | 决策 | 选择 | 放弃的方案 | 理由 |
|---|---|---|---|---|
| C5-1 | 消息存储 | DB 全量 + 窗口裁剪（M1） | 只存最近 N 轮 | 存全量为了回放审计，送窗口为了成本，两件事分开 |
| C5-2 | 窗口预算 | 按块分配，历史 3400 tokens | 全局 70% 触发 | 加证据/加提示时全局阈值会悄悄把历史挤没 |
| C5-3 | 摘要边界 | 一次挪到位，只向前 | 每轮微调 | 边界稳定 → 渲染逐字节一致 → prompt cache 能命中 |
| C5-4 | 指代消解 | 每轮做 + 三个短路（M3） | 按需触发 | 意图是阀门，阀门错了后面全错；短路只处理确定不需要的情况 |
| C5-5 | 跨轮状态 | DB 三字段，无 checkpointer（M4） | 只用 checkpointer | 前端要能查「未完成的申请」，且进程重启要恢复 |
| C5-6 | 恢复入口 | `classify_intent` 短路 | 新增条件边 | 保持主图形状不变，前四章结构测试继续有效 |
| C5-7 | 槽位合并 | 新值 `None` 不覆盖旧值 | 整体替换 draft | 用户这轮只补一个字段，不能把之前说的原因冲掉 |
| C5-8 | 改话题 | park + 尾部提示（M5） | 丢弃重置 / 强制完成 | 用户不用重来；提示只加一次，避免每轮唠叨 |
| C5-9 | 失效 | 30 分钟惰性清理（M7） | 定时任务扫描 | 读时判断保证正确性；脚本只做批量卫生 |
| C5-10 | 并发 | 进程内按会话串行（M8） | 允许并发 | 两条同时跑会互相覆盖 `pending_question` |
| C5-11 | ReAct 位置 | 只在 E 出口 | 全出口 / 只在 B | A 无选择、B 固定链、C 涉及资金、D 无决策；E 是唯一开放域 |
| C5-12 | ReAct 工具 | 4 个只读，代码白名单 | 复用全量工具注册表 | 模型不能选到写工具；白名单可静态测试 |
| C5-13 | ReAct 步数 | 4 步封顶，最后一步强制收敛 | 不封顶 | 一步一次模型调用；4 步够「查询 + 补查 + 回答」 |
| C5-14 | 兜底入口 | `gate` 判 `needs_tools` | 兜底一律 ReAct | 「你好」不该触发 4 步工具循环 |
| C5-15 | 工具结果 | 消息表存一行摘要，原文进 trace | 全量落 messages | 一次订单 JSON 就能吃光窗口；审计看 trace |
| C5-16 | `interrupt` | 本章不做（P5-12） | 引入 checkpointer | 详见「本章不做」 |
| C5-17 | 多流程 | 同一会话只留一个 `active_flow` | 多流程并存 | 两个流程抢同一个 `pending_question` 会把用户回答喂错；替换旧流程并记 trace |

---

## 测试与验证

### 单元测试

| # | 测试 | 断言 |
|---|---|---|
| 1 | 消息落库 | 返回 id；锚点 = `db-{id}`；重复 `client_message_id` 不重复插入 |
| 2 | 窗口构建 | 预算内全量；超预算裁最老；最新用户消息必留 |
| 3 | 摘要边界 | 只向前移；失败时旧摘要保留、窗口截断 |
| 4 | 槽位填充 | 订单号 / SKU / 规格透传 / 取消 / 恢复 / 抽不到 |
| 5 | draft 合并 | 新值 `None` 不覆盖；`condition_ok=false` 能覆盖旧 `None` |
| 6 | 指代消解 | 改写成功 / 守卫回落 / 异常回落 / 三个短路 |
| 7 | 超时 | 30 分钟内保留；31 分钟清空 |
| 8 | 锁 | 同会话串行；超时抛 `BusyError`；不同会话不互斥 |
| 9 | ReAct harness | 无工具直接答；一步工具；4 步封顶；工具错误回灌 |
| 10 | 白名单 | 写工具不可见；模型伪造 `user_id` 被丢弃 |
| 11 | 兜底 gate | 「你好」走脚本；含订单号走 ReAct |

### 集成测试

| # | 场景 | 断言 |
|---|---|---|
| 12 | 跨轮退款 | 第 2 轮补单号后继续判定，原因未被清空 |
| 13 | park 再回来 | 新话题正常答；流程三字段仍在；「继续退费」能接回 |
| 14 | 串轮污染 | 第 2 轮闲聊无退款字段 / 动作 |
| 15 | 主图形状 | 节点名与边不变；`fallback` 是子图 |
| 16 | 端到端 ReAct | 长尾问题调工具作答；≤4 步；trace 有 `react:step:N` |

**测试用临时库**（沿用 `tests/conftest.py` 的 `session` 夹具），不碰 `data/app.db`。

---

## 验收场景（演示脚本）

| # | 输入序列 | 期望 |
|---|---|---|
| 1 | 「我要退款，尺寸不合适」→「1002」 | 第 1 轮问单号；第 2 轮直接判定，不再问原因 |
| 2 | 「我要退款」→「退货政策是什么」→「继续退费」→「1002」 | 中间正常答政策；最后回到退款并完成 |
| 3 | 「我要退款」→「你好」 | 第 2 轮是闲聊，无退款金额/订单字段；只带 `resume_flow` 提示 |
| 4 | 「订单1001 到哪了」→「那单呢」 | 第 2 轮指代消解到 1001（或明确追问） |
| 5 | 「订单1001 的物流到哪了，顺便看下 ST-20 还有货吗」 | 当前分类会进 B，只答物流；直接调 E 子图可验证 ReAct 双工具 |
| 6 | 「你好」 | 不触发 ReAct，脚本回复 |

**延迟记录**（验收时写进 `docs/reports/ch05-acceptance.md`）：

| 场景 | 目标 | 说明 |
|---|---|---|
| 普通多轮 | P95 ≤ 6s | Ch03 基线 4.72s + 一次指代消解 |
| ReAct 兜底 | P95 ≤ 15s | 最多 4 次模型调用 + 工具 |
| 摘要触发轮 | 额外 ≤ 3s | 低频；失败不阻断 |

---

## 后续设计：ReAct 槽位解析（P5-14，本章不做）

解决「第一单」「那个推车」这类**自由表达指代**：

```
slot_fill 抽不到
  ↓
react.resolve_slot(pending_question, text, draft)
  ├─ 工具 list_user_orders(index?) → 候选订单
  ├─ 工具 search_products(keyword) → SKU 候选
  └─ ReactResult 增加 resolved_slot / resolved_value
  ↓
命中且唯一 → 回填 state，接回原流程
多命中     → select_order / select_sku 候选，不猜
未命中     → 按新话题 park
```

**边界**：

- 模型只决定「查什么」（序数/商品关键词），**不决定「是哪一个」**；
- `list_user_orders` / `search_products` 仍是只读、注入 `user_id`；
- 需要商品目录（`sku_id → 名称/别名/品类`），否则「推车」对应多个 SKU；
- 这是流程内的 resolver，**不是 E 出口的 gate**——E 仍只负责兜底回答。

**触发条件**：自然语言指代（序数/商品名）占追问回答 > 10%，或演示需要。

---

## 本章不做

| 不做 | 原因 / 触发条件 |
|---|---|
| **P5-10 · B 出口接 ReAct** | 仅当评测显示「参数抽取失败 / 需澄清」占比 > 20% |
| **P5-11 · 多意图澄清** | 需要分类器输出候选意图列表；当前无数据证明多意图占比高。触发：演示需要或兜底中多意图 > 5% |
| **P5-12 · checkpointer + `interrupt`** | DB 版跨轮已能跑通；`interrupt` 换的是「同一轮内断点续跑」，代价是 thread 生命周期与重跑语义。触发：需要人工审核插在流程中间 |
| **P5-14 · ReAct 槽位解析** | 需要 `ReactResult` 回填字段、两个只读工具与商品目录；当前用卡片点选。见上一节设计稿 |
| 三层渲染的层 2（工具结果半压） | 当前工具消息只存一行摘要，层 2 没有素材；等 ReAct 工具结果落原文再上 |
| 摘要段的语义筛选 | 摘要段 < 5 段，暂不需要 |
| 多实例锁 | 本章单进程；多 worker 要 Redis / DB 锁 |
| 重复请求的完整回放 | `client_message_id` 防重复落库；但同一请求重试仍会重跑图。触发：前端做自动重试 |
| 商品名 → SKU 模糊匹配 | 需要商品目录；当前数据只有型号 |
| 跨设备会话同步 | 不属于多轮上下文 |

### 为什么 `interrupt` 不是必需项

| 方案 | 恢复粒度 | 代价 |
|---|---|---|
| DB 三字段（本章） | 下一轮从入口重跑，靠 draft 接上 | 每轮多一次抽取调用 |
| checkpointer + `interrupt` | 同一轮从断点续跑 | thread 生命周期、状态清理、重跑语义 |

C 出口每轮本来就要调判定与回复两次模型；省下的一次抽取调用
换不来引入 checkpointer 的复杂度。等出现「人工审核必须插在流程中间」
这类需求再上（P5-12）。

---

## 风险与已知限制

| 风险 | 缓解 |
|---|---|
| park 后用户不知道有未完成流程 | 回答尾部加一次提示 + `resume_flow` 动作；不每轮重复 |
| 30 分钟对真实用户偏短 | 先按设计值；恢复率低或抱怨 > 20% 再调 |
| 30 分钟按「最后活动时间」刷新 | 用户持续聊别的话题时，旧流程不会过期（僵尸流程） | 先接受；出现僵尸流程就改为按 park 时间计时（加 `flow_parked_at`） |
| 摘要丢关键事实 | 边界只前移、原文保留；关键事实（订单号）也留在最近窗口 |
| 指代消解改错实体 | `raw_query` 永远保留；长度守卫；trace 存前后文 |
| ReAct 编造答案 | 只读工具 + 工具结果回灌 + 4 步封顶 + 失败退脚本 |
| ReAct 延迟不可控 | 步数封顶；每步超时；验收记录 P95 |
| 锁只在本进程有效 | 写进已知限制；多实例前先换分布式锁 |
| 历史读失败 | 降级为空历史继续本轮（日志 + trace），不 500 |
| 助手消息落库失败 | 不阻断回答：先保证用户看到回复，失败进日志；下一轮回放会缺这一条 |

---

## 与参考实现（MewHelp）的差异

| MewHelp | 本设计 | 理由 |
|---|---|---|
| 指代消解每轮改写 | 每轮 + 三个确定不需要的短路 | 省掉无意义调用，同时保留「阀门先补全」的原则 |
| 只有 `interrupt`，没有 park | **park + DB 三字段** | 用户改话题回来不用重来；参考实现会丢进度 |
| `business` 一个 ReAct 环管订单/物流/建单 | ReAct **只进 E 出口**，B/C 保持确定性 | 资金与数字必须可复算；ReAct 只承接长尾 |
| 工具注册表全量绑定，写工具靠确认流兜底 | **4 个只读工具，代码白名单** | 模型选不到写工具，比「选了再拦」少一条攻击面 |
| 工具原始结果进消息历史 | 消息存一行摘要，原文进 trace | 防止一次工具结果吃光上下文 |
| checkpointer 存图状态 | 暂不引入（P5-12） | 当前没有同轮断点续跑的需求 |
