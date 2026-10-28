# 任务拆分

> 上游：[实施计划](./00-overview.md) · [设计文档](../../design/)
> 粒度：**每个任务 ≤ 半天，可独立验收**
> 状态标记：`[x]` 已完成 · `[ ]` 待做

---

## P0 · 骨架与契约（已完成）

- [x] **P0-1** 项目结构 `app/{api,core,db,graph,tools}` —— 依据 D0
- [x] **P0-2** `pyproject.toml` + uv —— 依据 L5
- [x] **P0-3** `config.py` 启动校验 + `safe_summary()` —— 依据 L6、S5
- [x] **P0-4** `/api/meta` —— 依据 L1、K6
- [x] **P0-5** `/api/chat` 流式骨架 —— 依据 L1
- [x] **P0-6** trace_id 中间件 + JSON 日志 —— 依据 L7

---

## P1 · 数据层（1.5 天） · 对应 [Ch01](./ch01-data-foundation.md)

| # | 任务 | 依据 | 产出 | 验收 | 状态 |
|---|---|---|---|---|---|
| P1-1 | async engine + session 工厂（含 PRAGMA） | T7 | `app/db/session.py` | 能建表、能连接；外键与 WAL 生效 | ✅ |
| P1-2 | ORM models（**10 张表**：6 系统 + 4 业务） | M1/M4/K1/K4/K5/S1 | `app/db/models.py` | 与 `schema.sql` 字段一致 | ✅ |
| P1-3 | 建表脚本 + 索引验证 | K4 | `scripts/init_db.py` | 建表幂等；schema.sql 与 ORM 自动比对通过 | ✅ |
| P1-4 | conversations repository | M4 | `app/db/repo_conversation.py` | 能读写 `active_flow` 等三字段 | ✅ |
| P1-5 | messages repository | M1、C1、K4 | `app/db/repo_message.py` | 支持 `status` 与 `client_message_id`，重复键返回首次 id | ✅ |
| P1-6 | orders/stock/coupons 仓库（**落表，不 mock**） | S1、数据设计 | `app/db/repo_order.py` | **`user_id` 必填**，越权返回 NotFound | ✅ |
| P1-7 | seed 脚本 | 数据设计 §四/§五 | `scripts/seed.py` | 6 SKU / 20 订单 / 5 券，重复执行不翻倍 | ✅ |
| P1-8 | P1 测试 | S1 | `tests/test_models.py`、`test_repo_order.py`、`test_repo_message.py`、`test_seed.py` | 越权、必填参数、幂等索引、seed 覆盖度 | ✅ |

**阶段验收**：`python scripts/init_db.py && python scripts/seed.py` 后，`get_order("2001", user_id="u1")` 抛 NotFound。✅ 已验证

---

## P2 · 知识库与检索（1.5 天）

| # | 任务 | 依据 | 产出 | 验收 |
|---|---|---|---|---|
| P2-1 | 写 6 篇文档（returns/shipping/invoice/member/promo/products） | 数据设计 §二 | `data/kb/*.md` | 每篇 ≥2 个边界条件 |
| P2-2 | 法规 5 条清洗 + 元数据 | 数据设计 §三 | `data/kb/regulations/*.md` | 每条含 chapter/article/source |
| P2-3 | 切分脚本 | 数据设计 §八 | `scripts/build_chunks.py` | 文档按 `##`、法规按条 |
| P2-4 | 向量化入库 | — | `scripts/vectorize.py` | 能按 chunk id 查回 |
| P2-5 | 混合检索（dense + BM25 + RRF） | 数据设计 §八 | `app/core/retrieval.py` | 10 条知识题 hit@3 ≥ 0.8 |
| P2-6 | 证据闸（单阈值起步） | M3 讨论结论 | `app/core/gate.py` | 阈值可配置 |
| P2-7 | A 出口子图 | 意图文档 §二 | `app/graph/subgraph_knowledge.py` | 端到端能答政策题 |
| P2-8 | 知识路小评测 | — | `data/eval/knowledge_10.json` | 记录 hit@3 |

**阶段验收**：问「拆封的音像制品能退吗」→ 命中第六条 + 给出引用。

---

## P3 · C 出口状态机（3.5 天）

| # | 任务 | 依据 | 产出 | 验收 |
|---|---|---|---|---|
| P3-1 | 规则表设计（退款/价保/变更） | 数据设计 §六 | `app/rules/*.yaml` 或常量 | 每条规则可单独调用 |
| P3-2 | `compute` 纯函数 + 单测 | D1、B+ | `app/rules/refund.py` | 10 个用例覆盖正常/边界 |
| P3-3 | 状态机骨架（8 状态 15 转移） | D0、D1 | `app/graph/build_c.py` | 空实现可跑通 |
| P3-4 | `intake`：必填校验 | D1 | `app/graph/nodes.py` | 缺字段直接进 need_info |
| P3-5 | `extract`：LLM 结构化抽取 | D1 | 同上 | 抽不到时返回缺失列表 |
| P3-6 | `validate`：齐全 + 一致性 | D2 | 同上 | 矛盾数据（日期倒挂）能识别 |
| P3-7 | `need_info`：追问 + 恢复 | D3、M6 | 同上 | 补充后能接回 |
| P3-8 | `query_data`：调工具 | D1 | 同上 | 查不到走 need_info |
| P3-9 | `compliance`：红线 | D2 | 同上 | 触发后转人工 |
| P3-10 | `respond`：金额只复述 | D8、S6 | 同上 | 回答中的金额必来自 compute |
| P3-11 | audit trail → trace_span | L7 | `app/core/tracer.py` | 每个节点一条 span |
| P3-12 | 手算 10 份合同对拍 | L3 | `data/eval/refund_10.json` | 金额 10/10 一致 |

**阶段验收**：curl 一次完整退费，金额与手算一致；超时 P95 记录在案（C5）。

---

## P4 · B/D/E 出口（1.5 天）

| # | 任务 | 依据 | 产出 | 验收 |
|---|---|---|---|---|
| P4-1 | B 出口子图（参数校验 → 工具 → 结果校验） | 意图文档 | `app/graph/subgraph_data.py` | 查订单/物流/库存各 1 例 |
| P4-2 | 工具实现（身份注入 + 归属校验） | D7、S4 | `app/tools/*.py` | 模型传的 user_id 被覆盖 |
| P4-3 | D 出口 + `create_ticket`（确认 + 幂等） | D10、D11 | `app/tools/tickets.py` | 重复 request_id 只建一张 |
| P4-4 | E 出口三脚本 | 意图文档 §六 | `app/graph/nodes.py` | 三类输入各 1 例 |
| P4-5 | 兜底模板 + 落池 | 意图文档 §七 | `app/core/fallback.py` | 低置信问题进入 low_conf 表 |
| P4-6 | **B1 确定性工具链**：订单→物流两级依赖（先取 `tracking_no`）；缺 `order_id` 时列单让用户选 | 意图文档、D3 | `app/graph/subgraph_data.py` | 「订单 1001 到哪了」两步链跑通；无单号时 `list_user_orders` + interrupt 选单 |

**阶段验收**：五个出口各有一条 happy path。

---

## P5 · 多轮与 park（2 天）

| # | 任务 | 依据 | 产出 | 验收 |
|---|---|---|---|---|
| P5-1 | 消息落库 + `db-{id}` 锚点 | M1 | `app/core/history.py` | 锚点与 DB id 一致 |
| P5-2 | 窗口构建（两层） | M2 | 同上 | 超预算能裁剪 |
| P5-3 | 跨轮状态读写 | M4 | `app/core/session_state.py` | 三字段持久化 |
| P5-4 | 槽位填充 | M6 | `app/core/slot_fill.py` | 「1001」能接回流程 |
| P5-5 | park（改话题保留） | M5 | `app/graph/nodes.py` | 问别的再回来能续 |
| P5-6 | 超时清理（30 分钟） | M7 | `scripts/cleanup.py` | 过期后流程清空 |
| P5-7 | 会话级锁 | M8 | `app/core/lock.py` | 并发两条只有一条执行 |
| P5-8 | 演示场景 4 验收 | L8 | 手工记录 | 离开再回来完整走通 |
| P5-9 | **E 出口只读 ReAct**：harness（工具注册 / 循环 / 步数封顶 / trace）+ 兜底接入 | 架构取舍讨论 | `app/core/react.py`、`app/graph/fallback_agent.py` | 规则未命中的长尾能调工具作答；写操作不在工具集；超 4 步收敛到脚本 |
| P5-10 | *(可选)* **B2**：B 出口参数失败分支接 ReAct | 同 P5-9 | 复用 `app/core/react.py` | 仅当评测显示「参数抽取失败 / 需澄清」占比 > 20% 时做 |

**P5-9 / P5-10 的取舍**（依据：Agent 与 Workflow 边界讨论）

- **ReAct 只作"升级路径"，不作入口**：确定性方法判定搞不定时才进，主路径保持流水线。
- **A / C / D 出口不接 ReAct**：C 的金额与合规必须确定性（项目核心卖点），A 无选择空间，D 无决策空间。
- **触发条件**：兜底率 > 10% 做 P5-9；参数失败率 > 20% 才做 P5-10。未触发则不做的理由同样成立。

**阶段验收**：演示场景 4 通过；P5-9 完成后四条验收（长尾能作答 / 不调写工具 / 超步数收敛 / 越权不外泄）全过。

---

## P6 · 安全（1 天）

| # | 任务 | 依据 | 产出 | 验收 |
|---|---|---|---|---|
| P6-1 | 归属校验测试补全 | S1 | `tests/test_security.py` | 越权 3 例 |
| P6-2 | 会话归属 404 | S2 | `app/api/chat.py` | 猜 id 返回 404 |
| P6-3 | Prompt 四区结构 | S3 | `app/core/prompts.py` | 用户/检索/工具内容包裹 |
| P6-4 | 工具校验装饰器 | S4 | `app/tools/decorators.py` | 统一注入+校验 |
| P6-5 | 输出检查（金额/措辞） | S6 | `app/core/output_guard.py` | 违规回答被改写 |
| P6-6 | 日志收口 + 密钥检查 | S5 | `app/core/logging.py` | 日志里搜不到 key |
| P6-7 | 五个必测场景 | S1–S6 | `tests/test_security.py` | 全部通过 |

---

## P7 · 评测（2 天）

| # | 任务 | 依据 | 产出 | 验收 |
|---|---|---|---|---|
| P7-1 | 40 条评测集（7 桶） | 数据设计 §九 | `data/eval/eval_v1.json` | 每桶条数达标 |
| P7-2 | 客观 gold 手算 | L3 | 同上 | 金额/转人工有标准答案 |
| P7-3 | `kb_version` 内容哈希 | K6 | `app/core/kb_version.py` | 改文档后哈希变化 |
| P7-4 | 导入 EvalFlow 跑第一轮 | L1 | `docs/reports/run-01.md` | 指标产出 |
| P7-5 | 修失败样本跑第二轮 | — | `docs/reports/run-02.md` | 指标变化可解释 |
| P7-6 | 阈值扫描 | 置信度讨论 | 报告一节 | 误放/误拦曲线 |

---

## P8 · 前端（3 天）

| # | 任务 | 依据 | 产出 | 验收 |
|---|---|---|---|---|
| P8-1 | 前端脚手架（Vite + React） | L4 | `client/` | 起得来 |
| P8-2 | 聊天 + SSE 流式 | L4 | — | 流式渲染正常 |
| P8-3 | 进度面板 | L4 | — | 三状态可切换 |
| P8-4 | 转人工入口 | D4 | — | 点击可触发 |
| P8-5 | 断流重连 | C2 | — | 不重复、不跳变 |
| P8-6 | 四个演示场景验收 | L8 | 录屏 | 全部通过 |

---

## P9 · 交付（面试前）

| # | 任务 | 依据 | 验收 |
|---|---|---|---|
| P9-1 | README 定稿（≤150 行，含局限） | L8 | 五段齐全 |
| P9-2 | 三张截图 | L8 | 主界面/进度/评测报告 |
| P9-3 | 2 分钟视频 | L8 | 四个场景 |
| P9-4 | 仓库公开前检查 | L8-b | 无密钥、无真实数据 |

---

## 依赖关系（关键路径）

```
P1 ──► P2 ──► P3 ──► P4
        │      │
        └──────┴──► P7（评测需要 A 出口 + C 出口）
P1 ──► P5（多轮依赖会话与消息表）
P3 ──► P6（安全依赖工具与回答链路）
P4/P5 ──► P8（前端要有完整后端）
```

**可并行**：P2 与 P1 后半段可并行（写文档 vs 建表）；P8 前半段脚手架可提前。

---

## 与设计文档的对应

| 设计文档 | 主要落点 |
|---|---|
| 意图与出口 | P2-7、P4-1/4/5 |
| 数据与知识库 | P1-6/7、P2-1~4、P7-1 |
| 状态机与工具契约 | P3-3~11、P4-2/3 |
| 多轮与上下文 | P5 全部 |
| 并发与超时 | P5-6/7、P3-12 |
| 安全 | P6 全部 |
| 数据一致性 | P1-3、P7-3 |
| 数据飞轮 | P4-5（落池）、后续审核页 |
| 工程与交付 | P0、P7-4、P8、P9 |
