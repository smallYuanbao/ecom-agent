# Ch02 主流程骨架 Implementation Plan

> **For agentic workers:** 按 task 顺序执行，每个 task 完成即 commit。步骤用 `- [ ]` 追踪。

**Goal:** 立起主图，让 `/api/chat` 第一次端到端跑通：能分流、知识类能答并给引用、其余出口明确告知。

**Architecture:** LangGraph `StateGraph` 主图（resolve → classify → route），5 个出口：A 知识（真实子图）、D 转人工、E 兜底（真实），B/C 占位。

**Tech Stack:** LangGraph / Chroma / BGE-M3(Ollama) / rank_bm25+jieba / openai SDK。

**Spec:** `docs/superpowers/specs/ch02-walking-skeleton-design.md`
**前置:** Ch01 已交付（10 表 + seed + repository）

---

## Global Constraints

- **技术选型定死**：T1 LangGraph、T2 Chroma、T3 BGE-M3 本地、T4 rank_bm25+jieba、T5 openai SDK。走不通停下问。
- **占位三原则**：明确告知 / 统一节点 / 契约先定。**占位绝不返回金额。**
- **本章不做 rerank、不做指代消解**（透传）、不做多轮窗口。
- **本地环境（2026-10-29 实测）**：Chroma 1.0.0 跑在 Docker `:8000`（baby-ai 依赖，不动），Ollama `bge-m3` 1024 维可用。**本项目 FastAPI 用 8080**；Chroma 集合名 `ecom_kb` 且必须显式 `space=cosine`。详见 [本地环境现状](../../reports/local-env.md)。
- 涉及 LangGraph API，动手前用 Context7 核实当前版本接口（**版本对不上是返工重灾区**）。
- 不可单测的产出（prompt 质量）用"10 题评测 + 人工看 3 条"代替 TDD。
- 每个 task 完成即 commit。

---

### Task 1: 状态定义与主图骨架（全占位）

**Files:**
- Create: `app/graph/state.py`
- Create: `app/graph/build.py`
- Create: `app/graph/nodes.py`（先放占位节点）
- Create: `app/graph/routing.py`（路由表 + 条件边函数，静态数据，无依赖）
- Test: `tests/test_graph_smoke.py`

**Interfaces:**

```python
# state.py
class ConversationState(TypedDict, total=False):  # 字段见 spec
    ...

# routing.py
INTENT_TO_ROUTE: dict[str, str]      # 12 → 5
def route_by_intent(state) -> str

# build.py
def build_graph() -> CompiledGraph
```

**为什么路由表在 Task 1**：`build_graph` 的条件边必须有它才能建图。它是纯静态数据（12 条映射），没有任何依赖，提前建不影响后面任务——任务顺序按依赖排，不按"表属于路由"这种直觉排。

**Step 1: 状态**

按 spec 的字段子集实现，含 `is_placeholder` 标记。

**Step 2: 图（五个出口先全占位）**

**命名约定：节点名跟出口名，函数名加 `_exit` 后缀。**

| 节点名 | 函数名 | 谁替换 |
|---|---|---|
| `resolve_reference` | `resolve_reference` | Ch05 |
| `classify_intent` | `classify_intent` | Task 2 |
| `knowledge` | `knowledge_exit` | Task 7（换成子图） |
| `data` | `data_exit` | Ch04 |
| `rules` | `rules_exit` | Ch03 |
| `human` | `human_exit` | Task 8 |
| `fallback` | `fallback_exit` | Task 8 |

**节点名是契约**（出现在 trace、边、路由表、SSE 帧里，跨章不改），**函数是实现**（整块替换）。两者一一对应，看 trace 里的节点名能直接搜到函数。

出口函数加 `_exit` 后缀，是因为 `data` / `knowledge` / `fallback` 作为函数名太泛，容易和变量、模块重名；后缀同时表明它是"出口的收尾函数"。

```python
g = StateGraph(ConversationState)
g.add_node("resolve_reference", resolve_reference)      # 透传；Ch05 换真实实现
g.add_node("classify_intent", classify_intent)          # Task 2 换成真实分类
g.add_node("knowledge", knowledge_exit)                 # Task 7 换成知识子图
g.add_node("data", data_exit)                           # Ch04 换成真实查询
g.add_node("rules", rules_exit)                         # Ch03 换成 8 状态流水线
g.add_node("human", human_exit)                         # Task 8 换成真实话术
g.add_node("fallback", fallback_exit)                   # Task 8 换成三脚本
g.set_entry_point("resolve_reference")
g.add_edge("resolve_reference", "classify_intent")
g.add_conditional_edges("classify_intent", route_by_intent, {
    "knowledge": "knowledge", "data": "data", "rules": "rules",
    "human": "human", "fallback": "fallback",
})
for name in ("knowledge", "data", "rules", "human", "fallback"):
    g.add_edge(name, END)
```

**骨架期另外两个节点的占位形态**（`app/graph/nodes.py`）：

```python
async def resolve_reference(state):     # Ch05 换成真实指代消解
    return {"resolved_query": state.get("raw_query", "")}

async def classify_intent(state):       # Task 2 换成真实 12 类分类
    intent = "其他"
    return {"intent": intent, "intent_confidence": 0.0,
            "route": INTENT_TO_ROUTE.get(intent, "fallback")}
```

**不要往 state 里塞 `trace` 字段**：`ConversationState` 没有它，节点返回未声明的键会报错。
节点级诊断信息走 `trace_span`（Task 9 落库），不挤在图状态里。

**五个出口函数的占位形态**（`app/graph/nodes.py`）：

```python
PLACEHOLDER_TEXT = {
    "knowledge": "知识库检索正在开发中，您可以先转人工处理。",
    "data":      "订单与库存查询正在开发中，您可以先转人工处理。",
    "rules":     "退费核算正在开发中，您可以先转人工处理。",
    "human":     "转人工功能正在开发中，请稍后再试。",
    "fallback":  "我暂时没能理解您的问题，您可以换个说法，或转人工处理。",
}

def _placeholder(route: str) -> dict:
    """所有占位出口共用：明确告知 + 带转人工动作，绝不返回金额。"""
    return {"answer": PLACEHOLDER_TEXT[route],
            "is_placeholder": True,
            "actions": [{"type": "transfer_human"}]}


async def knowledge_exit(state):        # Task 7 整块替换
    return _placeholder("knowledge")

async def data_exit(state):             # Ch04 整块替换
    return _placeholder("data")

async def rules_exit(state):            # Ch03 整块替换
    return _placeholder("rules")

async def human_exit(state):            # Task 8 整块替换
    return _placeholder("human")

async def fallback_exit(state):         # Task 8 整块替换
    return _placeholder("fallback")
```

**为什么不共用单个 `placeholder` 函数**：五个出口将来各自演化（知识变子图、规则变流水线），**函数要能独立被替换**。共用一个是省了 20 行，代价是每次替换都得改分发逻辑。

> `knowledge` 节点在 Task 7 会换成**编译后的子图对象**（`g.add_node("knowledge", build_knowledge_graph())`）——节点名不变，挂的东西从函数变成图。这是 LangGraph 允许的，也正是"名是契约、实现可换"的体现。

**Step 3: 冒烟测试**

直接 `ainvoke` 一个状态，断言 `route` 有值、`is_placeholder` 为 True。

**验收**：`pytest tests/test_graph_smoke.py -q` 绿。

**Commit:** `feat(graph): 主图骨架（五出口全占位）`

---

### Task 2: 意图分类（12 类）

**Files:**
- Create: `app/core/llm.py`（openai SDK 封装）
- Create: `app/core/prompts.py`（意图 prompt）
- Modify: `app/graph/nodes.py`（`classify_intent` 换成真实实现）
- Test: `tests/test_intent.py`

**Interfaces:**

```python
INTENTS = ("政策问答","商品规格","优惠券活动","会员权益","订单查询","物流查询",
           "库存价格","退款申请","价保申请","订单变更","投诉","闲聊")

async def classify(query: str) -> tuple[str, float]   # (intent, confidence)
```

路由表 `INTENT_TO_ROUTE` 已在 Task 1 建好，本任务只读不改。

**Step 1: LLM 封装（function_calling 结构化）**

```python
def structured_call(schema: dict, messages: list[dict]) -> dict:
    tools = [{"type": "function", "function": {"name": "emit", "parameters": schema}}]
    for attempt in (1, 2):
        resp = client.chat.completions.create(
            model=..., messages=messages, tools=tools,
            tool_choice={"type": "function", "function": {"name": "emit"}},
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            return json.loads(msg.tool_calls[0].function.arguments)
    raise LLMFormatError()
```

**Step 2: 分类节点**

- prompt 里列出 12 类 + 边界规则（意图文档 §四的四对易混）
- 解析失败 / 越界 → `("其他", 0.0)`
- 置信度 < 0.5 → 仍按分类走，但 trace 标记 `low_conf`
- 节点返回 `{"intent", "intent_confidence", "route"}`——`route` 由 `INTENT_TO_ROUTE` 查表得到
  （条件边不能写 state，所以 state 里的 `route` 必须在这里写）

**Step 3: 测试（12 例）**

每类一句典型问法；允许 1 例失败但要在 commit message 记录。

**验收**：12 例中 ≥11 命中。

**Commit:** `feat(intent): 12 类意图分类（function_calling）`

---

### Task 3: 路由与占位节点

**Files:**
- Test: `tests/test_routing.py`
- （`routing.py` 与 `nodes.py` 已在 Task 1 建好；本任务**不改实现**，只补测试与文案复核）

**Interfaces:**

```python
INTENT_TO_ROUTE: dict[str, str]   # 12 → 5（Task 1 已建，不改）
def route_by_intent(state) -> str # 纯函数：只返回键，不写 state（Task 1 已建）
# 五个出口函数已在 Task 1 定义（knowledge_exit / data_exit / …），本任务不改名
```

**Step 1: 复核条件边函数的兜底语义**

```python
def route_by_intent(state) -> str:
    return INTENT_TO_ROUTE.get(state.get("intent", ""), "fallback")
```

用 `.get(默认值)` 而不是 `[]`：意图越界（模型返回没见过的词）时兜到 `fallback`，不抛 `KeyError`。**本步骤只确认写法，不新增代码。**

**Step 2: 占位文案定稿**

Task 1 已放好五个 `*_exit` 函数与 `PLACEHOLDER_TEXT`；本任务只需按 spec 复核文案，确认每条都**明确告知"开发中"且不含任何数字**。

**Step 3: 测试**

- 12 意图 → 5 出口的映射逐个断言
- **占位响应不得含金额**：对五个出口分别断言正则 `\d+\s*元` 无命中
- 每个出口返回的 `answer` 与 `PLACEHOLDER_TEXT[自己]` 一致（防止复制粘贴串行）

**验收**：映射 12 条 + 占位 5 条测试绿。

**Commit:** `test(graph): 12 意图 → 5 出口映射，占位出口不含金额`

---

### Task 4: 知识库切分与入库

**Files:**
- Create: `data/kb/*.md`（6 篇平台文档）
- Create: `data/kb/regulations/*.md`（法规 5 条）
- Create: `app/kb/loader.py`
- Create: `scripts/build_kb.py`
- Create: `app/core/kb_version.py`

**Interfaces:**

```python
def load_chunks() -> list[dict]        # {id, text, source, section/article}
def kb_version() -> str                # 内容哈希，前 12 位
```

**Step 1: 文档**

- 6 篇：returns / shipping / invoice / member / promo / products
- 法规 5 条：第 6/7/8/10/12 条，带 `chapter / article_no / source`

**Step 2: 切分**

- 文档按 `##`；法规按「第 X 条」
- chunk id 规则：`{source}#{section}` 或 `art-006`

**Step 3: 入库**

```python
collection = client.get_or_create_collection(
    name="ecom_kb",
    configuration={"hnsw": {"space": "cosine"}},   # 必须显式：默认 l2 会让证据闸语义反转
)
```

metadata 含 `source / section / kb_version`。

**集合名必须是 `ecom_kb`**：本地已有 `rag_docs` / `rag_medical` / `rag_samples` / `rag_user_uploads` / `user_profiles`，复用会维度不匹配或掺入无关数据。

**Step 4: 版本哈希**

对所有 chunk 的 `(id, text)` 排序后 sha256，取前 12 位。

**验收**：

```bash
uv run python scripts/build_kb.py
# 输出：chunks=NN, kb_version=xxxxxxxxxxxx
```

**Commit:** `feat(kb): 6 文档 + 5 法规切分入库，kb_version 哈希`

---

### Task 5: 混合检索（dense + BM25 + RRF）

**Files:**
- Create: `app/core/retrieval.py`
- Test: `tests/test_retrieval.py`

**Interfaces:**

```python
async def search(query: str, *, top_k: int = 5) -> list[dict]
# [{id, text, source, section, rrf_score, similarity}]
#   rrf_score  仅用于排序，别拿去比阈值（最大约 0.033）
#   similarity = 1 - distance，证据闸读这个
```

**Step 1: 两路**

```python
dense  = chroma.query(query, n=20)   # 返回 distance，须转 similarity
sparse = bm25_top(query, n=20)       # rank_bm25 + jieba 分词
```

**Step 2: RRF**

```python
rrf = sum(1 / (60 + rank) for rank in ranks_of(chunk))
```

**Step 3: 距离转相似度**

```python
def to_similarity(distance: float) -> float:
    return 1.0 - distance            # cosine distance = 1 - cosine similarity
```

**BM25 单独召回的 chunk 也要有 similarity**：按 chunk id 从集合里取回向量，现算一次 cosine（语料只有几十条，代价可忽略）。**不能让 similarity 为空**——否则闸对该 chunk 无法判定。

**Step 4: 测试**

- 同一 chunk 在两路都靠前 → 融合后仍靠前
- 专有名词（如「7 天无理由」）能被 sparse 单独召回
- **`to_similarity(0.2) == 0.8`**；`rrf_score` 的量级断言 < 0.05

**验收**：3 条测试绿；`search("退货政策")` 前 5 命中 returns 文档。

**Commit:** `feat(retrieval): dense+BM25+RRF 混合检索`

---

### Task 6: 证据闸与兜底落池

**Files:**
- Create: `app/core/gate.py`
- Create: `app/core/fallback.py`
- Test: `tests/test_gate.py`

**Interfaces:**

```python
THRESHOLD = 0.45          # 余弦相似度下限，不是 RRF 分数
def is_strong(hits: list[dict]) -> bool
def fallback_text(topic: str, top1_title: str | None) -> tuple[str, str]   # (text, next_step)
```

**Step 1: 闸**

```python
def is_strong(hits: list[dict]) -> bool:
    """空列表直接 False（fail-closed）。比较 similarity，不碰 rrf_score。"""
    return bool(hits) and hits[0]["similarity"] >= THRESHOLD
```

**为什么不用 `score` 单一字段**：RRF 分数最大值约 `2/61 ≈ 0.033`，与 0.45 差一个数量级，混用会让闸永远不通过、知识出口全部走兜底。

**Step 2: 兜底模板**

按意图文档 §七：`topic` 从 query 截取，`next_step` 二选一。

**Step 3: 落池**

调 Ch01 的 `low_conf_questions` 写入（`source='retrieval_low_conf'`，`retrieved` 三态）。

**验收**：闸 3 例（强/弱/空）；落池后能查到一行。

**Commit:** `feat(gate): 单阈值证据闸 + 兜底落池`

---

### Task 7: 知识出口（生成 + 引用）

**Files:**
- Create: `app/graph/knowledge.py`
- Create: `app/core/prompts.py`（`KB_ANSWER_PROMPT`）
- Test: `tests/test_knowledge_node.py`

**Interfaces:**

```python
def build_knowledge_graph() -> CompiledGraph   # retrieve → gate → generate | fallback
```

**Step 0: 接入方式（节点名不变）**

主图里把 `knowledge_exit` 换成子图对象，**节点名仍是 `knowledge`**：

```python
# build.py：这一行替换掉 Task 1 的 build_graph 中对应行
g.add_node("knowledge", build_knowledge_graph())   # 编译后的子图可直接当节点
```

子图与主图**共享 `ConversationState`**（同名字段自动传递）：子图读 `resolved_query`，写 `evidence / citation_ids / answer`，主图照常从 state 取用。

**Step 1: 子图**

```
retrieve → gate ─┬─ strong → generate
                 └─ weak   → fallback（模板 + 落池）
```

**Step 2: prompt（四区的前三区）**

```
[系统] 你是电商售后客服；只依据 <retrieved> 作答；不得编造金额与时效
[用户] <user_input>{query}</user_input>
[数据] <retrieved>[1] …[2] …</retrieved>
```

**Step 3: 引用解析**

从生成结果里提取 `[n]` → 映射回 `citation_ids`。

**验收**：10 题里 ≥8 题返回非空 `citations`；hit@3 ≥ 0.8（脚本 `scripts/eval_knowledge.py`）。

**Commit:** `feat(knowledge): 知识出口子图与引用解析`

---

### Task 8: D / E 出口

**Files:**
- Modify: `app/graph/nodes.py`
- Test: `tests/test_human_fallback.py`

**Step 1: D 出口（转人工）**

替换 Task 1 的 `human_exit` 函数体，**函数名与节点名都不变**：

```python
async def human_exit(state):                 # 节点名仍是 "human"
    return {"answer": "已为您转接人工客服，请稍候。",
            "actions": [{"type": "transfer_human"}]}
```

**Step 2: E 出口（三脚本）**

替换 `fallback_exit` 的函数体，**节点名仍是 `fallback`**；三个子分支（社交回应 / 确认致谢 / 无法处理）用内部小函数实现，不再拆成图节点。

**注意**：本章只有单轮，致谢脚本先按关键词命中。

**验收**：三类输入各 1 例；投诉类响应含 `transfer_human`。

**Commit:** `feat(exit): D 转人工与 E 兜底三脚本`

---

### Task 9: `/api/chat` 接图 + trace

**Files:**
- Modify: `app/main.py`（替换骨架实现）
- Create: `app/core/tracer.py`
- Modify: `app/api/chat.py`（如已拆分）
- Test: `tests/test_chat_sse.py`

**Interfaces:**

```python
async def run_turn(message: str, conversation_id: int | None, trace_id: str) -> AsyncIterator[dict]
```

**Step 1: 帧**

`ack → route → delta → citations → actions → done`；异常 → `error`（D9 四类码）。

**Step 2: trace**

每次请求：`trace_span` 写三条（`node:classify_intent` / `node:route` / `node:reply`），共享 `trace_id`。

**验收**：SSE 帧顺序正确；一次请求后 `trace_span` 有 ≥3 行同 `trace_id`。

**Commit:** `feat(api): /api/chat 接主图，SSE 帧 + trace`

---

### Task 10: 验收

- [ ] 五条端到端 curl 全部符合预期（含"规则类无金额"）
- [ ] `uv run pytest -q` 全绿
- [ ] `scripts/eval_knowledge.py` hit@3 记录在 `docs/reports/`
- [ ] `/api/meta` 返回 `kb_version`
- [ ] 更新 `00-tasks.md` 对应勾选
- [ ] 记录意图分类的实际准确率（供 Ch04 调边界用）
- [ ] **迷你质量传导表**：15 道题上把「检索是否命中」×「答案是否含 expect_points」列成 2×2，
      人工核验「**检索未命中但答案完整**」的那几条——那是编造风险最高的地方（Ch06 重点）
- [ ] 该表写入 `docs/reports/`，作为 P7-7 的第一次数据点

**Commit:** `test: Ch02 验收（骨架 + 知识出口 + 占位）`

---

## 与 spec 的对应

| spec 验收项 | 落在哪个 task |
|---|---|
| 走图、能看到 route | Task 1 / 3 / 9 |
| 知识类有答案 + 引用 | Task 4–7 |
| 规则类明确告知且无金额 | Task 3 / 10 |
| 投诉有 transfer 动作 | Task 8 |
| 三脚本命中 | Task 8 |
| trace ≥3 条 | Task 9 |
| `/api/meta` 有 kb_version | Task 4 / 9 |

## 风险

| 风险 | 应对 |
|---|---|
| LangGraph 版本 API 变化 | 动手前用 Context7 核实；先跑最小图验证 |
| 意图 12 类边界不准 | 允许 ≥11/12；记录错误样本，Ch04 后统一调 |
| 本地 Ollama 没装 | 检查 `ollama list`；没有则临时切 API（T3 已留双路） |
| **LLM 客户端跨测试复用报 `Event loop is closed`** | `get_client()` 用了 `lru_cache`，而 pytest-asyncio 每个测试一个新事件循环。conftest 加自动夹具：测试结束 `close()` + `cache_clear()`（生产只有一个循环，缓存本身是对的） |
| 模型返回清单外的意图词 | `classify()` 里做白名单校验，越界降级成「其他」；`route_by_intent` 再用 `.get(默认值)` 兜一层 |
| 引用解析失败 | 生成时要求固定格式 `[n]`；解析不到就返回空 citations（不报错） |
