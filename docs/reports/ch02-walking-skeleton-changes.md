# Ch02 · 主流程骨架 · 改动记录

> 对应 spec：[ch02-walking-skeleton-design.md](../superpowers/specs/ch02-walking-skeleton-design.md)
> 对应 plan：[ch02-walking-skeleton.md](../superpowers/plans/ch02-walking-skeleton.md)
> 格式说明见 [00-change-log-overview.md](./00-change-log-overview.md)

| # | 日期 | 任务 | commit |
|---|---|---|---|
| 1 | 2026-10-29 | 状态定义与主图骨架（五出口全占位） | `98abfb6` |
| 2 | | 意图分类（12 类） | |
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

| 文件 · 位置 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
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
- `uv run pytest tests/test_graph_smoke.py -q` → 4 passed
- `uv run pytest -q` → 34 passed（Ch01 的 30 条不受影响）
- `uv run ruff check app tests` → All checks passed
