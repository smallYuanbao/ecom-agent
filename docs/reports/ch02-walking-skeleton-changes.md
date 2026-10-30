# Ch02 · 主流程骨架 · 改动记录

> 对应 spec：[ch02-walking-skeleton-design.md](../superpowers/specs/ch02-walking-skeleton-design.md)
> 对应 plan：[ch02-walking-skeleton.md](../superpowers/plans/ch02-walking-skeleton.md)
> 格式说明见 [00-change-log-overview.md](./00-change-log-overview.md)

| # | 日期 | 任务 | commit |
|---|---|---|---|
| 1 | 2026-10-29 | 状态定义与主图骨架（五出口全占位） | `98abfb6` |
| 2 | 2026-10-29 | 意图分类（12 类） | `45f33f8` |
| 3 | 2026-10-29 | 路由与占位出口测试 | `e0d3787` |
| 4 | 2026-10-29 | 知识库切分与入库 | `f4bee0c` |
| 5 | 2026-10-30 | 混合检索（三策略实测选型） | `8152322` |
| 6 | 2026-10-30 | 证据闸与兜底落池 | `b7fd2fa` |
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

### 补记 · 多意图信号 `multi_intent`（Task 2 追加）

**触发**：实测「我买的推车能推吗？我要查推车物流」判成 `物流查询`、confidence **0.80**。
一句话两个诉求，模型只答一个却很有把握——**confidence 表达不了"歧义"这件事**。

**结论**：按此前「不支持组合查询」的决策，最终方案是**让用户二选一（澄清反问）**；
但澄清需要跨轮状态（`pending_question`），属 Ch05。本章只把**信号**产出来。

#### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `prompts.py` · `INTENT_PARAMETERS` | 只有 `intent` / `confidence` | 增加 `multi_intent: boolean`（必填），并给 `confidence` 加 description："只表示对选中意图的确定度" | 把"多诉求"塞进 confidence 会与"系统拿不准"混为一谈。两个诉求都很明确时 confidence 本该高，压到 0.6 以下反而失真 |
| `prompts.py` · `INTENT_SYSTEM` | 无多意图规则 | 新增「一句话里有多个诉求时选最主要的一个并置 `multi_intent=true`」 | 不加规则，模型只输出主意图，下游永远不知道漏掉了一个诉求 |
| `nodes.py` · `classify()` | 返回 `(intent, confidence)` | 返回 `(intent, confidence, multi_intent)`；降级路径固定 `False` | 信号要能被上层取到；降级时不能凭空宣称多意图 |
| `nodes.py` · `classify_intent` | 解包二元组 | 解包三元组，**丢弃** `multi_intent` | 本章单轮、无追问，写进 state 就是没有消费者的字段。契约先定，P5-11 再落 state |
| `scripts/try.py` · `run_intent` | 解包二元组 | 解包三元组，`multi_intent=True` 时额外打印一行 | 不跟着改就报 `ValueError: too many values to unpack` |

#### 为什么不是"降低 confidence"

| 方案 | 问题 |
|---|---|
| 多意图时把 confidence 压到 0.6 以下 | 与"句子模糊"共用同一个信号，下游无法区分该**追问澄清**还是该**让用户二选一** |
| **独立 `multi_intent` 字段（采用）** | 各管各的：`confidence` 管"拿不准"，`multi_intent` 管"没答全" |

#### 验证
- `uv run pytest -q` → **65 passed**（新增 2 条多意图用例；12 条单意图用例全部断言 `multi_intent=False`）
- 手工：
  - `我买的推车能推吗？我要查推车物流` → 物流查询 / 0.80 / **multi_intent=True**
  - `订单 1001 能退吗，顺便看看 ST-20 有货没` → 退款申请 / 0.80 / **multi_intent=True**
  - `退货政策是什么`、`我的快递到哪了` → 不出现多意图标记

---

## 3 · 路由与占位出口测试（`待填`）

本任务**只加测试，不改实现**——`routing.py` 在 Task 1 就建好了，五个 `*_exit`
在 Task 1 就写好了，这里把它们的行为钉死。

### 新增

| 文件 | 内容 |
|---|---|
| `tests/test_routing.py` | 36 条：3 类不变量 + 12 条分流 + 5 条越界兜底 + 5 组出口行为（每组 5 个出口） |

三类不变量（都是"改一处忘另一处"的高危点）：

| 不变量 | 漏了会怎样 |
|---|---|
| `set(INTENT_TO_ROUTE.values()) == set(EXIT_NODES)` | 路由表写了个图里不存在的出口 → 运行时报"找不到目标节点" |
| `set(PLACEHOLDER_TEXT) == set(EXIT_NODES)` | 加出口忘了加文案 → `_placeholder()` 抛 `KeyError` |
| 五个出口的 answer 两两不同 | 复制粘贴改了节点名、忘了改文案 → 用户看到张冠李戴的话 |

### 为什么这层不打图

`route_by_intent` 和五个 `*_exit` 都是纯函数，直接调用即可。打图会让测试变慢、
并引入 LangGraph 的依赖；接线是否正确由 `test_graph_smoke.py` 覆盖。
**纯函数层测逻辑，图层测接线，两层各测各的。**

### 验证
- `uv run pytest tests/test_routing.py -q` → **36 passed**
- `uv run pytest -q` → **102 passed**
- `uv run ruff check app tests scripts` → All checks passed

---

## 4 · 知识库切分与入库（`f4bee0c`）

### 新增

| 文件 | 内容 |
|---|---|
| `data/kb/returns.md` | 7 节：适用范围 / 不适用商品 / 完好判定 / 运费承担 / 赠品 / 到账时间 / 本文档不提供什么 |
| `data/kb/shipping.md` | 5 节：包邮门槛 / 发货时效 / 物流异常 / 运费险 / 不提供什么 |
| `data/kb/invoice.md` | 5 节：发票类型 / 开票时限 / 专票条件 / 修改重开 / 不提供什么 |
| `data/kb/member.md` | 5 节：三档等级 / 各档权益 / 积分规则 / 降级 / 不提供什么 |
| `data/kb/promo.md` | 6 节：券类型 / 门槛算法 / 叠加规则 / 有效期 / 退款后处理 / 不提供什么 |
| `data/kb/products.md` | 7 节：6 个 SKU 各一节 + 型号交叉点与选购提示 |
| `data/kb/regulations/seven-day-return.md` | 第六/七/八/十/十二条，**原文照录** gov.cn，未改写 |
| `app/kb/loader.py` | `cn_to_int()` / `split_sections()` / `load_chunks()` |
| `app/core/kb_version.py` | `kb_version()`：(id, text) 排序后 sha256 前 12 位 |
| `scripts/build_kb.py` | `ensure_collection()` / `embed_all()` / `existing_ids()` / `main()` |
| `tests/test_kb_loader.py` | 17 条：6 文档 5 法规齐全 / id 唯一 / 标题进 text / 排序稳定 / 条号转换 / 哈希确定性与敏感性 |

### 关键决策

| 决策 | 选择 | 备选与理由 |
|---|---|---|
| 切分规则 | **只按 `## ` 切，文档与法规共用** | 两套解析器：法规文件里每个 `## 第六条` 正好是一条，没必要写第二套 |
| 法规文本 | **原文照录 + 注明出处** | 自己改写：法规原文是合规依据，改写会失真；且政策文本不适用著作权（著作权法第五条） |
| 标题是否进 text | **进** | 只存正文：「本文档不提供什么」那一行会从**指令**变成一句**政策**（「不计算退款金额」被读成政策内容）；引用也不好自解释。**BM25 方面的收益有限，不要夸大**——长小节里标题会被正文稀释 |
| 条号解析 | **中文数字转整数，非法写法报错** | 宽松解析：`art-0零6` 这类脏 id 一旦进库，引用就永远对不上 |
| 库与语料的一致性 | **upsert 后删除多余 id** | 只 upsert：从文档里删掉的小节会永远留在库里，检索引到已不存在的内容 |
| 集合空间 | **建完回读校验是 cosine** | 只写不查：Chroma 默认 l2，写进去也是"成功"的，直到闸永远不通过才发现 |

### 验证
- `uv run python scripts/build_kb.py` → `chunks=40  kb_version=88bfd0a6a44b`
- 集合 `ecom_kb`：space=cosine、dim=1024、40 条
- 零向量查询返回 distance=1.0（cosine 空间下的正确值，l2 不会是这个数）
- 真实查询「退货运费谁承担」→ top1 `returns#退货运费由谁承担`，similarity **0.776**
- `uv run pytest -q` → **119 passed**

---

## 5 · 混合检索（`8152322`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/retrieval.py` | `tokenize()` / `embed()` / `cosine()` / `dense_search()` / `bm25_search()` / `rrf_fuse()` / `search()` |
| `scripts/eval_retrieval.py` | 三策略对比：Wilson 置信区间 + McNemar 不一致对数 + 失败样本 |
| `docs/reports/retrieval-eval.md` | 自动生成的原始数据 |
| `tests/test_retrieval.py` | 15 条：分词 / 余弦 / RRF 排序 / 契约字段 / 真实检索（服务不可用时跳过） |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `config.py` · 新增 `retrieval_strategy` | 无（策略写死在 spec 里） | `dense` 默认，可切 `bm25` / `hybrid` | 策略要能被数据推翻；写死就只能改代码 |
| `data/eval/*.yaml` · `expect_id` | 单个期望 chunk | **`expect_ids` 列表，任一命中即算对** | 「退货政策是什么」命中「不适用无理由退货的商品」也是对的。单值 gold 把正确答案判成失败，所有策略一起被低估 |
| `scripts/eval_retrieval.py` · ranks 键 | `case_id:style` | 按下标与 queries 对齐 | **同题两个 formal 问法键冲突，后者覆盖前者**，n 被悄悄少算 → 指标虚高 |
| `specs/ch02` 决策记录 | 「检索融合 = RRF，混合更好」 | 拆成三条：策略默认 dense、RRF 仅 hybrid 分支、BM25 保留但默认不用 | 原决策把「要不要混合」和「怎么融合」混成一条，前者从未被验证 |

### 实测结果

| 策略 | hit@3 | 95% CI | MRR | vs dense |
|---|---|---|---|---|
| **dense** | **1.00** | 0.92–1.00 | **0.960** | — |
| bm25 | 0.76 | 0.61–0.87 | 0.661 | 10:0（显著更差） |
| hybrid | 0.95 | 0.84–0.99 | 0.952 | 2:0（不显著） |

**分桶**：dense spec 9/9、regulation 9/9、policy 24/24；
hybrid 9/9、9/9、22/24；bm25 9/9、6/9、17/24。

**结论**：原假设「混合更好」不成立，但也没差到哪去——
hybrid 与 dense 的 MRR 几乎相同（0.935 vs 0.937），hit@3 差 1 条，
按「看不出差别就选简单的」默认用 dense。BM25/RRF 保留为配置项，
Ch07 扩到 100+ 条后复核（BM25 的价值在稀有词长尾，45 条覆盖不到）。

#### 中途修正：第一次测出的 BM25 = 0.71 是冤枉的

追查失败样本时发现 `多少钱包邮` 被切成了 `多少 / 钱包 / 邮`——
**「钱包」把「包邮」吃掉了**，`add_word` 和 `suggest_freq` 都压不住（实测过）。
改成「领域词先按字面整体抽出，再交给 jieba」后：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| bm25 hit@3 | 0.69 | **0.76** |
| bm25 MRR | 0.605 | 0.649 |
| hybrid MRR | 0.831 | **0.935** |

**教训**：方案表现差时，先查预处理，再下结论。
如果不追这一步，结论会写成「BM25 明显更差」，而真实原因有一半在分词。

### 关键设计点

- **分词必须定制**：jieba 默认把「退货运费」切成 `退 / 货运费`，把 `ST-20` 切成 `ST / - / 20`。
  型号、条号、金额都是字面精确匹配场景，所以型号与数字走正则，其余走 `lcut_for_search`。
- **RRF 分数不能当阈值**：`1/(60+rank)`，最大约 0.033，与 0.45 的闸阈值差一个数量级。
  测试里专门加了一条守卫（`test_rrf_score_is_far_below_evidence_threshold`）。
- **BM25 单独召回的块要补 similarity**：闸只认 similarity，补不上就等于无法判定。

### 验证
- `uv run python scripts/eval_retrieval.py` → 三行策略表（见上）
- `uv run pytest -q` → **159 passed**
- `uv run ruff check app tests scripts` → All checks passed

### 补记 · 共同短板分析（2026-10-30）

参考实现在评测里做了一件我们没做的事：**不只看"哪个策略最好"，还看"哪个桶所有策略都差"**——
所有方案都差的地方，问题不在策略，在语料或切分。

我们在 `scripts/eval_retrieval.py` 里加了 `common_weakness()`，报告多一节「共同短板」，
并在报告头部补了生成时间与 `kb_version`（可复现信息）。

#### 跑出来的第一个共同短板，以及三次归因

初版结论：**全策略失败** —— `试穿过了还能退吗`。

| 归因尝试 | 做法 | 结果 |
|---|---|---|
| ① 语料缺这句话 | 在「商品完好的判定」补一条"试穿属于查验" | ❌ 没动，正确答案仍排第 18 |
| ② 切分粒度过粗（一节混了四个子话题） | 拆成「商品完好的判定」+「视为商品不完好的情形」 | ❌ 仍排第 16 |
| ③ **gold 标错了** | 把 `art-007` 也加入 P3 的 `expect_ids` | ✅ 命中 |

第 ③ 条才是真因：`art-007` 写的是「**一经激活或者试用后价值贬损较大的商品**…可不适用」，
而查询是「**试穿**过了还能退吗」——**art-007 在这题上是合理答案**，它正是判断
"试穿是否导致贬值"的依据。原来只认 `商品完好的判定` / `art-008`，把这个正确结果判成了失败。

**两次归因都错在"改了再试"，没有先验证假设。**
正确的顺序应该是：先看**被召回的是什么、为什么它合理**，再决定改语料还是改 gold。

#### 结果

| 指标 | 修 gold 前 | 修 gold 后 |
|---|---|---|
| dense hit@3 | 0.98（41/42） | **1.00（42/42）** |
| hybrid hit@3 | 0.95 | 0.95 |
| 全策略失败题 | 1 条 | **0 条** |

`dense` 到 1.00 是**天花板信号**：这批题已经无法再区分策略，
选型结论停在"dense 够用、hybrid 并列"即可。**Ch07 必须加难度**
（多跳、指代、同一问题的不同问法之外的变形），否则评测没有信息量。

#### 语料改动保留

拆成两节虽然没提升指标，但「完好」与「不完好」分开本身是更清晰的知识组织，
对生成侧的引用展示也更友好，所以保留（chunk 数 40 → 41，`kb_version` 变为 `a8b084ac29b9`）。
**但要说清楚：这次改动没有解决任何指标问题，别把它当成"有效优化"。**

---

## 6 · 证据闸与兜底落池（`b7fd2fa`）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/gate.py` | `is_strong()` / `explain()`；docstring 记录阈值推导与扫描表 |
| `app/core/fallback.py` | `fallback_text()` / `record_low_conf()` / `_retrieved_payload()` |
| `scripts/eval_gate.py` | 判定矩阵 + 阈值扫描（0.45–0.66） |
| `docs/reports/gate-eval.md` | 自动生成 |
| `tests/test_gate.py` | 17 条：闸边界 / 判定依据 / 兜底话术 / 落池三态 |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `config.py` · `evidence_threshold` | 0.45（计划里的拍脑袋值） | **0.55**（实测校准） | 0.45 在扫描里只能拦住 6/11 负样本——**连拒答题都会被放行**，闸等于没有 |
| `config.py` · 注释 | 无说明 | 写明实测区间与已知漏洞 | 阈值是个魔法数字，不留推导过程，下次没人敢动 |
| `gate.py` · 比较对象 | 早期版本比 `score`（RRF 分数） | 只比 `similarity` | RRF 最大约 0.033，比 0.55 永远不通过（Task 5 已修，此处固化） |

### 关键决策：0.55 还是 0.58

阈值扫描给出了明确的两难：

| 阈值 | 可答题放行 | 拒答+域外拦住 |
|---|---|---|
| 0.45 | 42/42 | **6/11** |
| **0.55** | **42/42** | **10/11** |
| **0.58** | 41/42 | **11/11** |
| 0.62 | 35/42 | 11/11 |

`0.58` 能拦住全部负样本，代价是把「FB-30 保修多久」（0.566）也拒掉。

**选 0.55**：被拒的那条是合法且常见的型号类问题；漏掉的那条（能分期付款吗）
在生成阶段还有一道防线——prompt 要求"只依据检索作答"，证据里没有分期信息
时模型应当拒答。**这条假设不能白纸黑字当结论，由 Task 10 的质量传导表验证。**

### 设计点

- **`explain()` 记录 top1 的 id 与分数**：只有分数没有 id 的话，池子里看不出
  「是阈值定高了」还是「语料真缺这块」。
- **`retrieved` 三态不可混**：`"[]"` 零命中 = 语料缺口；`"[{...}]"` 有召回但分低 =
  召回质量问题；`NULL` = 根本没走检索。混成一个会污染飞轮的优先级判断。
- **落池只存 id + 分数**，不存原文：原文在向量库里，池子里再存一份是重复，
  且语料一改就过期。

### 验证
- `uv run python scripts/eval_gate.py` → 可答题 42/42 放行、该拒答 3/3 拦住、域外 7/8 拦住
- 唯一错误放行：`能分期付款吗` → 0.572 `member#积分规则`（已知漏洞，已记录）
- `uv run pytest -q` → **179 passed**
