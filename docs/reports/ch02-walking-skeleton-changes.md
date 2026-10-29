# Ch02 · 主流程骨架 · 改动记录

> 对应 spec：[ch02-walking-skeleton-design.md](../superpowers/specs/ch02-walking-skeleton-design.md)
> 对应 plan：[ch02-walking-skeleton.md](../superpowers/plans/ch02-walking-skeleton.md)
> 格式说明见 [00-change-log-overview.md](./00-change-log-overview.md)

| # | 日期 | 任务 | commit |
|---|---|---|---|
| 1 | 2026-10-29 | 状态定义与主图骨架（五出口全占位） | `98abfb6` |
| 2 | 2026-10-29 | 意图分类（12 类） | `45f33f8` |
| 3 | | 路由与占位出口测试 | |
| 4 | | 知识库切分与入库 | |
| 5 | | 混合检索 | |
| 6 | | 证据闸与兜底落池 | |
| 7 | | 知识出口子图 | |
| 8 | | D / E 出口 | |
| 9 | | `/api/chat` 接图 + trace | |
| 10 | | 验收 | |

---

## 1 · 状态定义与主图骨架（`98abfb6`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/graph/state.py` | `ConversationState(TypedDict, total=False)`，12 字段按「输入 / 中间产物 / 输出」三组注释 |
| `app/graph/routing.py` | `INTENT_TO_ROUTE`（12 意图 → 5 出口）、`FALLBACK_ROUTE`、`route_by_intent()` |
| `app/graph/nodes.py` | `resolve_reference` / `classify_intent`（占位）/ `PLACEHOLDER_TEXT` / `_placeholder()` / `knowledge_exit` / `data_exit` / `rules_exit` / `human_exit` / `fallback_exit` |
| `app/graph/build.py` | `EXIT_NODES` 常量 + `build_graph() -> CompiledStateGraph` |
| `tests/test_graph_smoke.py` | 4 条：图能编译 / 走图落在出口 / 五个出口节点已注册 / 占位文案不含金额 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `pyproject.toml` · dependencies | 无编排框架 | 加 `langgraph`（实际解析到 1.2.18） | T1 定死 LangGraph；主图 + 条件边 + 后续子图都依赖它 |
| `nodes.py` · `resolve_reference` 返回值 | plan 原稿：`{"resolved_query": ..., "trace": {"coref": "passthrough"}}` | 只返回 `{"resolved_query": ...}` | `ConversationState` 没有 `trace` 键。节点返回未声明的键，LangGraph 合并状态时会报错；节点级诊断信息应由 `trace_span` 落库（Task 9），不挤进图状态 |
| `nodes.py` · `classify_intent` 占位 | 无（新文件） | 一律判「其他」→ `route = fallback` | 占位期必须**保持"判不出来就兜底"的语义**。若随手返回某个具体意图，冒烟测试会误以为分类链路已通，也违背"占位不假装成功" |
| `build.py` · 条件边映射 | plan 原稿：手写 `{"knowledge": "knowledge", ...}` 五个键 | 由 `EXIT_NODES` 生成 `{route: route for route in EXIT_NODES}` | 手写映射与 `EXIT_NODES` 是两份数据，加出口时容易只改一处；从同一常量派生则不可能漂移 |

### 关键设计点

- **节点名 = 出口名，函数名加 `_exit` 后缀**：节点名跨章不变（出现在 trace / 边 / 路由表 / SSE 帧），函数体逐章替换。
- **单一来源的路由表**：`classify_intent` 写 `state["route"]` 与条件边 `route_by_intent` 读同一张 `INTENT_TO_ROUTE`，state 与实际分支不会不一致。
- **五个出口各自成函数**：共用单个 `placeholder` 省 20 行，但替换时得改分发逻辑；独立函数可整块替换。

### 验证
- `uv run pytest tests/test_graph_smoke.py -q` → 17 passed
- `uv run pytest -q` → 34 passed（Ch01 的 30 条不受影响）
- `uv run ruff check app tests` → All checks passed

---

## 2 · 意图分类（12 类）（`45f33f8`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/llm.py` | `LLMFormatError` / `get_client()`（`lru_cache`）/ `structured_call()`（`tool_choice` 强制 function_calling，重试 1 次，`temperature=0`，`timeout=30s`） |
| `app/core/prompts.py` | `INTENT_SYSTEM`（12 类 + 四对易混 + 拿不准选「其他」）/ `INTENT_PARAMETERS`（enum 派生自路由表）/ `intent_messages()`（用户输入用 `<user_input>` 包裹） |
| `tests/test_intent.py` | 12 例真实模型（每类一句）+ 4 条 mock 降级路径（格式错误 / 越界意图 / 回落原话 / route 一致性） |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `nodes.py` · `classify_intent` | 占位：一律判「其他」，置信度 0 | 调 `classify()` 做真实 12 类分类，写 `intent` / `intent_confidence` / `route` | Task 2 的目标就是把占位换掉；再往下走 Task 3 的路由测试需要真实意图才能验证 |
| `nodes.py` · 新增 `classify()` | 无 | 一次 LLM 调用 + **白名单校验**：返回值不在 `INTENT_TO_ROUTE` 里就降级「其他」 | 模型会返回清单外的词（如「退款」）。只靠 `route_by_intent` 的 `.get(默认值)` 兜底，`state["intent"]` 里会留下一个不存在的意图，污染日志与后续统计 |
| `nodes.py` · 异常处理 | 计划原稿：只说"解析失败/越界 → 其他" | 只捕获 `LLMFormatError`；网络/鉴权异常**上抛** | 一律吞掉会把"密钥配错"伪装成"所有问题都答不上来"，排查时被误导。格式错误可恢复，服务错误不可恢复，必须分开 |
| `routing.py` · 新增 `INTENTS` / `DEFAULT_INTENT` | prompt 里的 12 类与代码里的路由表各写一份 | 从 `INTENT_TO_ROUTE` 派生 | 两份清单会漂移（改了路由表忘了改 prompt）。派生后不可能不一致 |
| `tests/conftest.py` · 新增自动夹具 | 无 | 每个测试结束 `close()` + `cache_clear()` LLM 客户端 | `get_client()` 用 `lru_cache`，而 pytest-asyncio 每测试一个新事件循环；缓存的 httpx 连接属于旧循环，下一个测试直接报 `Event loop is closed`（首轮 6 个测试因此失败） |
| `tests/test_graph_smoke.py` · 数据来源 | 直接 `ainvoke` 真实流程 | `monkeypatch` 钉死分类结果，12 意图逐个验证分流 | 接了真实分类后，冒烟测试会变成"每次都调模型"：慢、要密钥、结果有抖动。**接线测试与模型测试要分开** |

### 关键设计点

- **异常分两层**：`LLMFormatError` → 降级成安全默认值；网络/鉴权 → 上抛由 API 层回 `error` 帧。吞掉后者会让配置问题伪装成业务问题。
- **清单单一来源**：`INTENTS` 从 `INTENT_TO_ROUTE` 派生，prompt 的 enum 再从 `INTENTS` 派生，三层只有一份真数据。
- **测试分层**：`test_intent.py` 用真模型（准不准），`test_graph_smoke.py` 用 mock（通不通）。

### 验证
- `uv run pytest tests/test_intent.py -q` → **16 passed（12/12 命中，验收线 ≥11）**
- `uv run pytest -q` → **63 passed**
- `uv run ruff check app tests` → All checks passed
- 连通性预检：`chat.completions` 与 `tool_choice` 强制调用均正常返回

### 补记 · 测试说明注释（无逻辑变更）

| 文件 · 符号 | 补了什么 |
|---|---|
| `tests/test_intent.py` · 模块 docstring | pytest 的收集规则（目录/文件名/函数名前缀）、`parametrize` 展开成 12 条、`async def` 由 pytest-asyncio 包装、`skipif` 是 skip 不是 fail；两类测试的分工表 |
| `tests/test_intent.py` · 三条 mock 用例上方 | monkeypatch 的两条要点：**为什么不走网络**（调的是模块全局里的名字）、**为什么 patch 使用处而不是定义处**（`from ... import` 新建独立绑定）；以及自动还原的机制 |
| `tests/test_intent.py` · `boom` / `weird` / `capture` | 说明各自模拟什么、`*_args` 下划线前缀表示"接收但故意不用" |
| `tests/test_graph_smoke.py` · `force_intent` | 与 `test_intent` 同一套 mock 机制；说明 patch 的是使用处 |
