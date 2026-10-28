# Ch02 设计：主流程骨架与知识出口（行走骨架）

> 实施计划：[ch02-walking-skeleton.md](../plans/ch02-walking-skeleton.md)

## 目标

**立起主图，让 `/api/chat` 第一次端到端跑通**：一句话进来能被分流，知识类能答，其余出口要么真做要么明确告知。

验收标准：

1. `POST /api/chat` 走图：响应中能看到 `route`，且落在五个出口之一。
2. **知识类**（「退货政策是什么」）→ 返回答案 + `citations`，引用能在知识库查到。
3. **规则类**（「这单能退多少」）→ 明确回「退费核算开发中，可转人工」，**响应中不得出现任何金额**。
4. **投诉/要人工** → 安抚话术 + `actions: [{"type":"transfer_human"}]`。
5. **闲聊 / 致谢 / 无法处理** → 三个脚本各自命中（E 出口）。
6. 每次请求有 `trace_id`，至少落三条 `trace_span`（入口 / 意图 / 路由）。
7. `/api/meta` 返回 `kb_version`（知识库内容哈希）。

## 技术栈（引用既定选型）

| 项 | 选择 | 依据 |
|---|---|---|
| 状态机 | **LangGraph** | T1 |
| 向量库 | **Chroma** | T2 |
| Embedding | **BGE-M3 本地（Ollama）** | T3 |
| 关键词检索 | **rank_bm25 + jieba** | T4 |
| 模型调用 | **openai 官方 SDK**（function_calling 抽意图） | T5 |

**本章不用**：rerank、指代消解、多轮窗口、缓存。

## 架构

```
POST /api/chat
      │
   StateGraph
      ├─ resolve_reference   ← 本章透传（Ch05 实现）
      ├─ classify_intent     ← 12 类，规则 + LLM
      └─ route_by_intent ──┬─ knowledge  → 子图：retrieve → gate → generate
                           ├─ data       → 占位（Ch04 换成真实）
                           ├─ rules      → 占位（Ch03 换成真实）
                           ├─ human      → 真实：话术 + transfer 动作
                           └─ fallback   → 真实：三脚本
```

## 目录结构（本章新增）

```
app/
  graph/
    state.py           # ConversationState（D6 B+ 字段子集）
    build.py           # 主图 + 条件边
    nodes.py           # 分类 / 路由 / 占位 / D / E
    knowledge.py       # 知识出口子图
  core/
    llm.py             # openai SDK 封装（function_calling 结构化）
    prompts.py         # 四区结构 + 各节点 prompt
    retrieval.py       # dense + BM25 + RRF
    gate.py            # 证据闸（单阈值）
    kb_version.py      # 知识库内容哈希
    tracer.py          # trace_id → trace_span
  kb/
    loader.py          # 文档/法规读取与切分
scripts/
  build_kb.py          # 切分 + 向量化入库
  eval_knowledge.py    # 10 题 hit@3 小评测
data/
  kb/*.md              # 6 篇平台文档
  kb/regulations/*.md  # 法规 5 条
  chroma/              # 向量库（gitignore）
```

## 状态（D6 B+ 子集）

```python
class ConversationState(TypedDict, total=False):
    trace_id: str
    conversation_id: int | None
    raw_query: str
    resolved_query: str        # 本章 = raw_query（透传）
    intent: str                # 12 类之一或"其他"
    intent_confidence: float
    route: str                 # knowledge / data / rules / human / fallback
    evidence: list[dict]       # 检索命中的 chunk（本章允许全量，Ch05 收窄）
    citation_ids: list[str]    # 如 ["第六条", "returns#退款时效"]
    answer: str
    actions: list[dict]        # transfer_human 等
    is_placeholder: bool       # 占位标记，便于统计与测试
```

**注意**：`evidence` 在本章存全量（Ch01 的 D6 结论是 B+，但 A 出口的生成需要原文）。**Ch05 引入窗口后，evidence 只保留 citation_ids + 摘要。**

## 路由表（12 意图 → 5 出口）

| 出口 | 意图 |
|---|---|
| `knowledge` | 政策问答、商品规格、优惠券活动、会员权益 |
| `data` | 订单查询、物流查询、库存价格 |
| `rules` | 退款申请、价保申请、订单变更 |
| `human` | 投诉 |
| `fallback` | 闲聊；**「其他」为默认值**（分类失败/越界） |

路由是 `dict` 查表，不写 if-else。

## 检索与证据闸

**切分**：

- 平台文档：按 `##` 标题切，chunk 带 `source / section`
- 法规：按「条」切，chunk 带 `article_no / chapter / source`

**检索**：

```
dense top20（Chroma）+ BM25 top20（rank_bm25）→ RRF(k=60) → top5
```

**不做 rerank**：语料几十到几百 chunk，先测 hit@3；低于 0.8 再考虑（触发条件记入后续优化）。

**证据闸（单阈值起步）**：

```python
THRESHOLD = 0.45            # top1 分数下限，常量可调

def gate(hits) -> bool:
    return bool(hits) and hits[0]["score"] >= THRESHOLD
```

**不通过** → 走兜底话术（模板 + 变量），并落 `low_conf_questions`。

## 生成与引用

**prompt 四区（S3 的一半，Ch06 补完整）**：

```
[系统指令]  你是电商售后客服；不得编造金额与时效
[用户输入]  <user_input>…</user_input>
[检索数据]  <retrieved>[1] …[2] …</retrieved>
```

**要求**：生成时对关键结论标注 `[n]`，服务端解析出 `citation_ids` 返回。

**兜底模板**（意图文档 §七）：

```python
FALLBACK = "关于「{topic}」，我这边没有找到确切的依据，不能给您结论。已经为您记录，{next_step}"
# next_step = "您可以点击「转人工」联系客服。" / "您是不是想问「{top1_title}」？"
```

## 接口契约

### POST /api/chat（SSE）

请求：

```json
{"message": "退货政策是什么", "conversation_id": null, "client_message_id": "uuid"}
```

响应帧（每帧 `data: {...}\n\n`）：

| 帧 | 内容 |
|---|---|
| `{"event":"ack"}` | 已受理 |
| `{"event":"route","route":"knowledge"}` | 分流结果（便于前端/调试） |
| `{"delta":"…"}` | 文本增量 |
| `{"event":"citations","items":[…]}` | 引用 |
| `{"event":"actions","items":[…]}` | 转人工等动作 |
| `{"event":"done"}` | 结束 |
| `{"event":"error","code":"…","message":"…"}` | 错误（D9 四类） |

### GET /api/meta

补 `kb_version`（内容哈希），供 EvalFlow 做快照（K6）。

## 测试与验证

| # | 测试 | 断言 |
|---|---|---|
| 1 | 意图分类 12 例 | 每类一句，全部命中（可容忍 1 例失败并记录） |
| 2 | 路由 5 例 | 12 意图正确落到 5 出口 |
| 3 | **占位不产生金额** | 规则类问题响应里正则 `\d+\s*元` 无命中 |
| 4 | 知识问答 10 题 | hit@3 ≥ 0.8；至少 8 题能给出引用 |
| 5 | 兜底三脚本 | 闲聊/致谢/无法处理各命中对应话术 |
| 6 | 转人工动作 | 投诉类响应含 `transfer_human` |
| 7 | trace | 一次请求产生 ≥3 条 span，`trace_id` 一致 |

**端到端验收（curl）**：

```bash
curl -N -X POST localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"message":"退货政策是什么"}'
# 期望：route=knowledge → delta → citations → done

curl -N ... -d '{"message":"我这单能退多少"}'
# 期望：route=rules → 明确告知开发中，且无金额
```

## 本章不做

| 不做 | 归属章节 |
|---|---|
| C 出口 8 状态流水线 | Ch03 |
| B 出口订单/物流/库存查询 | Ch04 |
| 多轮窗口、摘要、park、指代消解 | Ch05 |
| 安全全套（会话归属、输出检查、注入防御完善） | Ch06 |
| 评测接入 EvalFlow | Ch07 |
| 前端 | Ch08 |
| rerank、白话摘要、检索三段式 | 后续优化（有触发条件） |

## 关键决策记录

| 决策 | 选择 | 备选与理由 |
|---|---|---|
| 章节顺序 | **主图骨架提前到 Ch02** | 按层推进到底（原计划）：前 9 天无完整流程，集成风险后置 |
| 占位实现 | **统一节点 + 明确告知** | 返回假数据：会被误当真实现，最危险 |
| 意图分类时机 | **Ch02 实现 12 类** | 只实现 A 出口需要的 4 类：主图不完整，路由无法验证 |
| 意图返工 | **接受 Ch04 后调一轮边界** | 一次做对：边界规则要等真实出口才能验证，不现实 |
| 检索融合 | **RRF(k=60)** | 线性加权：baby-ai 已验证 RRF 更稳（量纲问题） |
| rerank | **本章不做** | 一开始就上：先测 hit@3，不达标再加 |
| 证据闸 | **单阈值 0.45** | 四信号加权：无标注数据，先跑通再校准 |
| prompt 结构 | **先搭四区中的三区** | Ch06 才补工具结果区与输出校验 |
| evidence 存储 | **本章存全量** | 与 D6 的 B+ 冲突：A 出口生成需要原文，Ch05 收窄为 citation_ids |
