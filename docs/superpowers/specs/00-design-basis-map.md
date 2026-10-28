# 设计依据图谱（依据 → 推导 → 落点）

> 目的：**每一条实现内容都能追溯到一条依据；每一条依据都能落到一个落点。**
> 这份文档先于章节 spec 存在，章节只是它的展开。

---

## 一、依据清单（四类）

### A. 业务依据（决定做什么）

| # | 依据 | 来源 |
|---|---|---|
| A1 | 面向**消费者（C 端）**的电商售后客服 | L0 产品定位 |
| A2 | 核心场景是**退费核算**：能不能退、退多少、依据哪条 | 意图文档、数据设计 |
| A3 | 高风险域：承诺错误会造成资损与纠纷 | S6、compliance 设计 |
| A4 | 无真实用户、数据为合成 | 项目约束 |

### B. 技术依据（决定怎么做的边界）

| # | 依据 | 来源 |
|---|---|---|
| B1 | 主流程沿用"意图分流 + 多出口"的范式，具体设计自己定 | 项目立项时的决定 |
| B2 | 不复制任何课程源码；无 LICENSE 的代码一行不抄 | 归属约束 |
| B3 | 单人开发，优先零运维 | L5、K1 |
| B4 | 可评测：必须能被 EvalFlow 接入 | L1 |
| B5 | 可观测：trace_id 贯穿 | L7 |

### C. 参考依据（只借范式，不借实现）

| # | 借什么 | 不借什么 |
|---|---|---|
| C1 | Workflow + Agent 混合的思路 | 九类意图 → 五个出口的具体切分 |
| C2 | 证据闸（生成前判断证据够不够） | 四信号加权与具体权重 |
| C3 | 飞轮（落池 → 审核 → 补库） | 三入口的具体定义 |
| C4 | interrupt 做追问、权限在节点校验、interrupt 前只读 | 具体代码 |
| C5 | 负面知识 + 标准替代表述 | 具体 prompt 文案 |

### D. 已有资产（决定怎么省力）

| # | 资产 | 用途 |
|---|---|---|
| D1 | EvalFlow（评测平台） | Ch07 直接接入，不重造 |
| D2 | baby-ai（RAG 实现经验） | 检索链路、降级、多租户的现成经验 |
| D3 | ai-learning-notes（67 篇笔记） | 面试讲法、技术判断的依据 |

---

## 二、推导规则（怎么从依据得到设计）

四条规则，用来检查"有没有凭空长出来的设计"：

| # | 规则 | 反例（违规的样子） |
|---|---|---|
| R1 | **每个表/字段必须有决策依据** | "先加个 `tags` 字段以后可能有用" |
| R2 | **每个模块必须对应至少一条验收** | "写个 utils 以后会用" |
| R3 | **每个章节必须对应一组决策**，不能是"顺便做的" | "顺手把日志也接了" |
| R4 | **不做的项必须写明理由**（不能只是没做） | 微调：没写为什么不做 |

---

## 三、推导结果

### 3.1 系统边界

```
做：
  意图分流（12 意图 / 5 出口）
  C 出口 8 状态流水线（退费/价保/变更）
  A 出口检索 + 证据闸
  B 出口工具查询
  D/E 出口（转人工 / 兜底）
  多轮 + park
  安全（归属/注入/承诺）
  评测接入

不做（有理由）：
  微调（成本高、无标注数据）
  MCP（工具 3–5 个，硬编码够）
  B 端坐席台（后续配套）
  RAG 缓存（K2：单人低 QPS）
  多租户（单用户场景）
```

依据：A1–A4、B1、B3；不做项见各文档。

### 3.2 数据模型（10 张表 ← 逐表对应依据）

| 表 | 由哪条依据推出 | 关键字段的依据 |
|---|---|---|
| `conversations` | M4 跨轮状态 | `active_flow/pending_question/draft` ← M4；`deleted_at` ← K5；`summary` ← M2 |
| `messages` | M1 存储 | `status` ← C1；`client_message_id` ← K4；`tool_calls` ← 回放需求 |
| `tickets` | D 出口 | `request_id` ← D11 幂等 |
| `trace_span` | L7 可观测 | `parent_span_id` ← span 模型 |
| `faq` | 飞轮写回 | `sync_status` ← K1；`source_*` ← K5 溯源 |
| `low_conf_questions` | 兜底落池 | `retrieved` 三态 ← 意图文档 §七 |
| `orders` | 数据设计 §五 | `user_id` ← S1 归属校验 |
| `logistics` | B 出口 | 与 orders 关联 |
| `stock` | B 出口 | 规格组合 ← 商品设计 |
| `coupons` | A 出口 | `threshold/expires_at` ← 优惠券规则 |

**检查**：10 张表全部有依据，没有"预留表"。

### 3.3 模块划分（app/ 下 ← 对应依据）

| 模块 | 职责 | 依据 |
|---|---|---|
| `api/` | HTTP 契约（chat/meta/health） | L1 |
| `core/` | 日志、检索、闸门、输出检查等横切能力 | L7、S3/S6 |
| `graph/` | 状态机与出口子图 | D0–D3 |
| `tools/` | 工具实现（身份注入 + 校验） | D7、S4 |
| `db/` | session、models、repository | S1、M1 |
| `rules/` | 退款/价保/变更规则 | D1、数据设计 §六 |

**检查**：没有 `utils/` 这类无主模块——每个目录都能说清"为什么它在"。

### 3.4 章节划分（8 章 ← 由依赖推出）

| 章 | 为什么排在这个位置 | 依据 |
|---|---|---|
| Ch01 数据地基 | 一切依赖存储；归属校验也要在此落地 | M1/M4/K1/K4/K5、S1 |
| Ch02 知识库与检索 | A 出口的前置；与 Ch01 可并行 | 数据设计 §二/三/八 |
| Ch03 C 出口状态机 | 核心价值；依赖 Ch01 的订单与规则 | D0–D12 |
| Ch04 B/D/E 出口 | 补齐四出口；复用 Ch01 工具与 Ch03 的守卫 | 意图文档 |
| Ch05 多轮与 park | 依赖 Ch01 三字段 + Ch03 的 need_info | M1–M8 |
| Ch06 安全 | 依赖完整链路才能做越权/注入/承诺测试 | S1–S6 |
| Ch07 评测 | 依赖 A+C 出口都能跑 | L1–L3、K6 |
| Ch08 前端 | 依赖后端全部就绪 | L4 |

**检查**：每章都有"前置依赖"，没有可以提前跳过的章。

---

## 四、追溯矩阵（决策 → 落点）

| 决策 | 落点（表 / 模块 / 章节） |
|---|---|
| D0 主图+子图 | `graph/` · Ch03 |
| D1 8 状态 | `graph/build_c.py` · Ch03 |
| D2 只做高风险守卫 | `graph/nodes.py` · Ch03 |
| D3 独立 need_info | 同上 · Ch03/Ch05 |
| D4 分级 + fail-closed | `graph/routing.py` · Ch03 |
| D5 支持恢复 | checkpointer · Ch03 |
| D6 B+ 状态 | `ConversationState` · Ch03 |
| D7 身份注入 | `tools/` · Ch04 |
| D8 结构化出参 | `tools/` · Ch04 |
| D9 四类错误码 | `tools/errors.py` · Ch04 |
| D10 写操作确认 | `tools/tickets.py` · Ch04 |
| D11 request_id 幂等 | `tickets.request_id` · Ch01/Ch04 |
| D12 分级超时重试 | `tools/engine.py` · Ch04 |
| M1 存储 + 锚点 | `messages` 表 · Ch01/Ch05 |
| M2 窗口与摘要 | `core/history.py` · Ch05 |
| M3 指代消解 | `core/coref.py` · Ch05（可延后） |
| M4 跨轮状态 | `conversations` 三字段 · Ch01/Ch05 |
| M5 park | `core/session_state.py` · Ch05 |
| M6 槽位填充 | `core/slot_fill.py` · Ch05 |
| M7 30 分钟清理 | `scripts/cleanup.py` · Ch05 |
| M8 会话锁 | `core/lock.py` · Ch05 |
| C1 取消 + finally 落库 | `api/chat.py` · Ch03/Ch05 |
| C2 断流续传 | `api/chat.py` · Ch08 |
| C3 限流 | 中间件 · Ch06 |
| C4 后台任务并发 | `core/summary.py` · Ch05 |
| C5 30s 总超时 | `api/chat.py` · Ch03 |
| C6 client_message_id | `messages` 索引 · Ch01 |
| S1 数据层归属 | `db/repo_order.py` · Ch01 |
| S2 会话 404 | `api/chat.py` · Ch06 |
| S3 注入隔离 | `core/prompts.py` · Ch06 |
| S4 工具校验 | `tools/decorators.py` · Ch06 |
| S5 密钥不进日志 | `core/logging.py` · Ch01/Ch06 |
| S6 输出承诺 | `core/output_guard.py` · Ch06 |
| K1 向量同步 | `faq.sync_status` · Ch02 |
| K2 缓存 | 不做（后续） |
| K3 跨表事务 | `db/` 事务边界 · Ch03 |
| K4 唯一索引 | `schema.sql` · Ch01 |
| K5 软删 + 溯源 | `deleted_at` / `faq.source_*` · Ch01 |
| K6 版本基线 | `/api/meta` · Ch01/Ch07 |
| L0 C 端定位 | 全局 · 所有章节 |
| L1 接 EvalFlow | `/api/chat` `/api/meta` · Ch01/Ch07 |
| L2 手动触发 | 流程约定 · Ch07 |
| L3 gold 手算 | `data/eval/` · Ch07 |
| L4 聊天+进度 | `client/` · Ch08 |
| L5 本地 venv | `pyproject.toml` · Ch01 |
| L6 配置校验 | `config.py` · Ch01 |
| L7 三层可观测 | `core/logging.py` + `trace_span` · Ch01/Ch06 |
| L8 文档演示 | README/录屏 · 收尾 |

**检查**：67 条决策全部有落点，无悬空决策。

---

## 五、未决项（推导过程中暴露出来的技术选型）

写这份图谱时发现，有几项**技术选型还没正式定**，但章节 spec 必然要用到：

| # | 待定项 | 选项 | 影响 | 建议 |
|---|---|---|---|---|
| T1 | 状态机实现 | LangGraph / 自写状态机 | Ch03 整体结构 | **LangGraph**（D0 的子图、D5 的 checkpointer 都依赖它） |
| T2 | 向量库 | Chroma / sqlite-vec / FAISS | Ch02 | **Chroma**（baby-ai 经验，零运维） |
| T3 | Embedding | BGE-M3 本地（Ollama）/ API | Ch02 | 取决于本机是否有 Ollama；没有则用 API |
| T4 | BM25 | rank_bm25 + jieba / 自己实现 | Ch02 | **rank_bm25 + jieba**（语料小，够用） |
| T5 | LLM SDK | OpenAI 兼容 SDK | Ch03 | **openai** 官方 SDK，base_url 指向 DeepSeek |
| T6 | 前端框架 | React + Vite | Ch08 | 用你最熟的（frontend 出身，无争议） |
| T7 | 异步 SQL | aiosqlite + async SQLAlchemy | Ch01 | 已按此写骨架 |

**T1–T5 需要在写 Ch02/Ch03 spec 之前定**，否则 spec 里会写出"待定"。

---

## 六、这份图谱怎么用

| 场景 | 用法 |
|---|---|
| 写新章节 spec | 先从第四章矩阵里取该章的决策，再展开 |
| 有人问"为什么要这张表" | 查 3.2 表，一行对应一条依据 |
| 想加新功能 | 先问"它对应哪条依据"，找不到就说明是范围蔓延 |
| 面试被问"设计怎么来的" | 讲 A/B/C/D 四类依据 + R1–R4 四条推导规则 |

**核心检查**：任何新加的内容，要么能指向一条已有依据，要么先补一条依据。**不允许"先写代码，再补理由"。**
