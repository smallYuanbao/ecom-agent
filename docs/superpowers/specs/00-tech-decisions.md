# 技术选型决策（T1–T7）

> 状态：**待确认**
> 说明：这七项**目前不是依据**——它们是写章节 spec 前必须定下来的前提。
> 定完之后，请同步更新 [依据图谱](./00-design-basis-map.md) 的「五、未决项」。
> 格式与 D / M / C / S / K / L 一致：选项 → 推荐 → 理由 → 反方 → 你的选择。

---

## T1 · 状态机实现

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. LangGraph** | `StateGraph` + checkpointer + interrupt | D0 子图、D5 恢复、D3 追问所需能力原生支持 | 引入框架依赖，调试略黑盒 |
| B. 自写状态机 | 转移表 + 手写恢复逻辑 | 零依赖、完全可控 | 恢复、中断、并发态都要自己实现 |
| C. 纯流程函数 | 顺序函数调用，无图 | 最简单 | 分支一多就不可维护，无法画图 |

**推荐：A**

理由：

1. D0 选了「主图 + 子图」，LangGraph 的 `add_node`/`add_edge` 天然支持
2. D5 选了「支持恢复」，checkpointer 是现成的
3. D3 选了独立 `need_info`，中断恢复依赖 `interrupt`
4. 参考实现的生产经验证明这套可行（**借范式，不借代码**）

**反方理由**：框架升级会带来破坏性变更（参考实现就踩过 SDK 版本差异）；自写状态机在 8 个状态规模下也不算大。

**影响章节**：Ch03、Ch04

**你的选择**：_A_____

---

## T2 · 向量库

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. Chroma** | 嵌入式，本地持久化 | 零运维；元数据过滤现成；baby-ai 已验证 | 依赖体积较大；不擅长大规模分布式 |
| B. sqlite-vec | SQLite 扩展 | 与主库同文件，最少组件 | 生态较新；元数据过滤要自己写 SQL |
| C. FAISS | 纯索引库 | 快、轻 | 不存元数据、不持久化，要自己管 id 映射 |

**推荐：A**

理由：语料只有几十到几百 chunk，**零运维比性能重要**；元数据过滤（按 `kb_version`、`source_type`）是现成能力。

**反方理由**：如果最终想把 SQLite 做到"一个文件装下所有"，sqlite-vec 更干净。

### 为什么不是 Milvus（及替换条件）

Milvus 2.4+ **原生支持 sparse 向量 + hybrid search + 内置 RRF**，确实能省掉 T4 里的 rank_bm25 与手写融合。没列为选项的原因：

| 理由 | 依据 |
|---|---|
| standalone 需要 etcd + MinIO + Milvus 三个容器 | 违背 **B3 零运维** |
| 语料几十到几百 chunk，用不上分布式能力 | 规模不匹配 |
| 原生 RRF 是黑盒，讲不清 k=60、归一化与融合的取舍 | **可解释性**（面试要讲） |

**替换触发条件（满足任一，才评估迁移）**：

| # | 触发条件 | 说明 |
|---|---|---|
| 1 | chunk 数 **> 10 万** | 应用层 BM25 + 融合开始有性能压力 |
| 2 | 需要**多副本 / 分布式**检索 | Chroma 单机嵌入式不支持 |
| 3 | 检索延迟成为瓶颈，且已确认卡在融合层 | 先做 profile，别猜 |

**为将来留口**：检索层定义接口，Chroma 只是其中一个实现。

```python
class Retriever(Protocol):
    async def search(self, query: str, *, top_k: int, filters: dict) -> list[Chunk]: ...

class ChromaRetriever: ...      # 现在
# class MilvusRetriever: ...    # 将来，业务代码不动
```

**注意**：若将来改用 Milvus 原生 hybrid，**T4（rank_bm25 + jieba）可整体删除**——这是两条互斥的路径。

**影响章节**：Ch02

**你的选择**：___A___

---

## T3 · Embedding 模型

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. BGE-M3 本地（Ollama）** | 本地起 Ollama，1024 维 | 免费、离线、数据不出门；与 baby-ai 一致 | 占内存；首次加载慢 |
| B. API Embedding | 调云端接口 | 无需本地资源 | 有成本；数据出网 |
| C. sentence-transformers 小模型 | 本地 Python 加载 | 灵活选模型 | 依赖重、下载模型 |

**推荐：先检测本机是否有 Ollama → 有则 A，无则 B**

```bash
ollama list | grep -i bge   # 有输出 → 选 A
```

**反方理由**：A 需要本地资源，笔记本风扇会转；但换来的是"离线可跑"，演示时不怕断网。

**影响章节**：Ch02

**你的选择**：__A____

---

## T4 · BM25 实现

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. rank_bm25 + jieba** | 现成库 + 中文分词 | 少写 100 行；分词质量够用 | 不支持增量更新，需重建 |
| B. 自写 BM25 | 自己算 IDF/TF | 完全可控、可教学演示 | 容易写错，且要自己测 |
| C. 只用 dense | 不做混合检索 | 最简单 | 专有名词召回差 |

**推荐：A**

理由：语料小（几十 chunk），重建索引成本可以忽略；jieba 对中文商品/政策术语分词够用。

**反方理由**：rank_bm25 是纯内存结构，索引要每次重建；如果语料上千条，启动会变慢。

**影响章节**：Ch02

**你的选择**：___A___

---

## T5 · LLM SDK

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. openai 官方 SDK** | `OpenAI(base_url=...)` 兼容 DeepSeek 等 | 最薄一层；坑都在自己代码里，看得见 | 结构化输出要自己拼 |
| B. httpx 手写 | 直接发 HTTP | 零 SDK 依赖 | 流式解析、重试、错误码全要自己处理 |
| C. LangChain `ChatOpenAI` | 走 LangChain 抽象 | 与 LangGraph 生态天然兼容 | 多一层抽象，出问题难定位 |

**推荐：A**

理由：

1. **LangGraph 只管编排，不强制用 LangChain 的模型层**
2. **坑可见**：参考实现选了 C，仍然要子类化框架内部方法才能修 usage 统计（210 行包装）——**抽象不等于少踩坑，只是把坑换了个位置**
3. 它踩出来的三条经验（见下）与平台无关，选 A 一样能照搬

### 参考实现的做法（对照）

它用 `langchain_openai.ChatOpenAI`，写了一个 210 行的 `llm.py`，做了四件事：

| # | 问题 | 做法 |
|---|---|---|
| 1 | 流式 usage 被重复累加（24 chunk × 11 → 264） | 子类化 `_convert_chunk_to_generation_chunk`，把累计值换成增量 |
| 2 | 结构化输出被模型多吐的思维链带崩 | `structured()` 强制走 `function_calling`，不用 `json_schema` |
| 3 | 某些模型流式不吐 `tool_calls` | `needs_non_streaming_tools()` 强制非流式 |
| 4 | 不同用途要不同模型/通道 | `resolve_slot(slot)` |

**四条都是实测踩出来的**，注释里带具体数字。

### 三条可照搬的经验（选 A 也适用）

**① 结构化只用 function_calling**

```python
tools = [{"type": "function", "function": {"name": "emit", "parameters": schema}}]
resp = client.chat.completions.create(
    model=..., messages=..., tools=tools,
    tool_choice={"type": "function", "function": {"name": "emit"}},
)
data = json.loads(resp.choices[0].message.tool_calls[0].function.arguments)
```

**② 偶发失败原地重试一次，不降级**

```python
for attempt in (1, 2):
    try: return parse(call())
    except (NoToolCall, JSONDecodeError): continue
raise            # 两次都失败抛给调用点，由调用点降级成安全默认值
```

> 参考实现的原话：「别在这里降级到一条更差的通路上」。

**③ 中转通道优先流式**

非流式的中转层可能把 `tool_calls` 的 arguments 拼坏；流式反而正常。

**反方理由**：若 Ch03 大量使用 LangChain 的 message / tool 类型，C 能省掉类型转换；且 LangGraph 与 LangChain 的 message 天然兼容。

**影响章节**：Ch03、Ch06

**你的选择**：__A____

---

## T6 · 前端框架

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. React + Vite** | 你熟悉的栈 | 迭代快；SSE 处理简单 | 需要自己搭流式与状态管理 |
| B. Next.js | React 全栈框架 | 有 SSR/路由约定 | 本项目不需要 SSR，反而增加概念 |
| C. 原生 HTML + fetch | 无构建 | 零依赖 | 状态一多就难维护 |

**推荐：A**

理由：这是你的强项，**前端投入产出比最高**；本项目是单页应用，不需要 Next 的服务端能力。

**反方理由**：C 能更快出 demo；但进度面板、多轮状态、断流重连用原生写会很痛苦。

### 请求层统一封装（配合 T6）

**前端不直接调 axios / fetch，统一走两个文件**：

| 文件 | 用途 | 技术 | 为什么分开 |
|---|---|---|---|
| `src/lib/http.ts` | JSON 接口（meta / health / 会话列表） | axios 实例 | 拦截器、错误映射统一 |
| `src/lib/sse.ts` | 流式对话 `/api/chat` | **fetch + ReadableStream** | **axios 不支持流式响应**（会缓冲整段） |

#### http.ts 统一做什么

| 能力 | 说明 | 对应设计 |
|---|---|---|
| `baseURL` / `timeout` | 默认 10s（对话不走这里） | — |
| 注入 `X-Trace-Id` | 前端生成，便于和 `core/logging.py` 的 trace_id 对齐 | L7 |
| 统一错误映射 | 见下表 | D9 |
| 响应解包 | 直接返回 `data`，调用方不写 `.data` | — |

**错误映射表（对应 D9 四类错误码）**：

| HTTP | 场景 | UI 行为 |
|---|---|---|
| 404 | 会话不存在 / 无权限 | 清空当前会话，回到新会话（不提示"无权限"，避免泄露） |
| 429 | 触发限流（C3） | 提示「操作太频繁」，禁用发送 5 秒 |
| 5xx / 超时 | 服务端异常 | 显示「稍后再试」+ 重试按钮，**保留用户输入不清空** |

#### sse.ts 统一做什么

| 能力 | 说明 | 对应设计 |
|---|---|---|
| POST `/api/chat` | 自动带 `client_message_id` | K4 |
| 帧解析 | `data: {"delta":...}` / `{"event":"done"}` / interrupt | L1 |
| `AbortController` | 用户点停止、离开页面时中止 | C1 |
| 断流重连 | 记录 `last_offset`，重连先补历史再继续 | C2 |

#### client_message_id 的生成规则（关键）

```ts
// 同一条用户消息的所有重试必须复用同一个 id
const clientMessageId = crypto.randomUUID();
await sendMessage({ text, clientMessageId });   // 重试时传同一个
```

**每次重试都新生成 = 幂等失效**——服务端会把它当成两条不同的消息。

#### 目录与依赖

```
client/src/lib/
  http.ts       # axios 实例 + 拦截器
  sse.ts        # fetch 流式 + 重连
  api.ts        # 业务接口（chat / sessions / meta），只调上面两个
```

**规则：组件里不允许出现 axios 或 fetch，只能调 `api.ts`。**

**影响章节**：Ch08

**你的选择**：__A____

---

## T7 · 异步数据库访问

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. async SQLAlchemy + aiosqlite** | 全异步 | 与 FastAPI 事件循环一致；换 PG 只改 URL | async ORM 有学习曲线 |
| B. 同步 SQLAlchemy + 线程池 | 同步代码跑在线程里 | 写法简单、调试直观 | 上下文切换开销；容易阻塞事件循环 |
| C. 裸 sqlite3 | 手写 SQL | 最轻 | 无 ORM，迁移与类型都没保障 |

**推荐：A**

理由：Ch03 的状态机会在节点里频繁读写（每步写 trace_span），**同步调用会阻塞整个事件循环**；而且 K3 的事务边界用 ORM 表达更清楚。

**反方理由**：async SQLAlchemy 的 session 生命周期容易出错（比如在后台任务里复用 session）。

**影响章节**：Ch01、Ch03

**你的选择**：______

---

## 定完之后要做的两件事

1. 把 [依据图谱](./00-design-basis-map.md) 的「五、未决项」改成「已定」，并补进第四章追溯矩阵
2. 在 Ch01/Ch02/Ch03 的 spec 里可以直接引用「T1 = A」这样的编号，不必重复论证

---

## 汇总表（填完后更新）

| # | 决策 | 选择 |
|---|---|---|
| T1 | 状态机实现 | ______ |
| T2 | 向量库 | ______ |
| T3 | Embedding | ______ |
| T4 | BM25 | ______ |
| T5 | LLM SDK | ______ |
| T6 | 前端 | ______ |
| T7 | 异步 SQL | ______ |
