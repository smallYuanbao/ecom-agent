# Ch07 评测接入设计

> 依据：L1–L3、K6、数据设计 §九、Ch02–Ch06 验收、
> EvalFlow `external_babyai` 契约与 Ch26 外部目标设计。
> 范围：**评测集 / 评测专用门面 / EvalFlow 适配 / 指标与阈值 / 四份报告**。
> 状态：设计稿（2026-11-07）。C7-1（评测门面）、C7-2（两条评测线）已确认。

---

## 目标

1. 有一份 **44 条、7 桶**的评测集，每条有可执行的期望（路由 / 字段 / 金额 / 断言）
2. 对外暴露一个**非流式评测门面** `/api/eval/chat`，返回回答、引用证据、完整 top-k
3. EvalFlow 能以 `EVAL_TARGET=ecom` 跑通全链路评测，并把被测系统状态写进 run 快照
4. 产出 **run-01 / run-02** 两份报告，指标变化可解释
5. 产出阈值扫描曲线、质量传导 2×2、退款困难组（n ≥ 20，带 Wilson 95% CI）
6. 所有门槛可由命令复现；评测门面默认关闭、不污染演示库

### 验收标准

1. `eval_v1.yaml` 通过校验：44 条，7 桶条数达标，id 唯一，`split` 标明 dev/test
2. `/api/eval/chat` 返回 `answer` / `references` / `hits` / `route` / `signals`；
   `hits` 带 `rank`、`score`、`cited`
3. `ENABLE_EVAL_API` 未开启时，端点返回 404；演示 `/api/chat` 行为不变
4. EvalFlow 导入数据集并跑完第一轮，run 快照含 `kb_version` / 模型 / prompt 版本
5. run-02 只修有归因的失败样本；指标变化能指到具体改动
6. 阈值扫描给出「误放率 / 误拦率」曲线，同时给出语义闸模拟结果，并标注当前 0.55
7. 质量传导 2×2 四格都有样本（自然数据不足时用受控降级补齐，并写清每格来源）；
   「检索未命中但答案完整」人工核验 ≥ 3 条
8. 退款困难组 n ≥ 20，每类 ≥ 3 条；报告含 Wilson 95% CI 与 per-class 准确率
9. 全量测试通过；评测集、导入脚本、适配器都有测试

---

## 技术栈

- 沿用 Ch01–Ch06，**ecom 侧不引入新依赖**（导出脚本用已有 `httpx`）；
- EvalFlow 沿用 FastAPI + Celery + Redis + React；
- judge 仍由 EvalFlow 提供，**不在 ecom 里再写一套**；
- 业务硬指标（金额、路由、转人工）用本地确定性脚本，不用模型判。

---

## 一、两条评测线

评测分两层，解决的是不同问题：

| 线 | 入口 | 特点 | 回答什么问题 |
|---|---|---|---|
| **层内评测** | 现有 `scripts/eval_*.py` 直接跑子图 | 快、确定性、可单测 | 检索 / 闸 / 生成 / 判定哪一层坏了 |
| **端到端评测** | `/api/eval/chat` ← EvalFlow | 慢、全链路、有快照 | 用户最终拿到什么；改一个配置整体变好还是变坏 |

```
层内（诊断）                                端到端（验收）
eval_retrieval  ─┐                          ecom :8081
eval_gate       ─┼─ docs/reports/*.md       /api/eval/chat ◀── EvalFlow :8002
eval_knowledge  ─┤                          /api/meta      ◀── run 快照
eval_refund     ─┘                          run-01 / run-02 / gate
```

**为什么保留两套**：

- 层内脚本只跑子图，能把「检索没召回」与「模型没用上证据」分开；
- 端到端跑主图，能看到意图分类、追问、转人工、输出检查的真实组合；
- 只用端到端，失败后无法归因；只用层内，测不到用户实际体验。

**不合并**：层内脚本继续存在，Ch07 不改它们的语义，只复用数据与报告。

---

## 二、评测集

### 2.1 配比

`data/eval/eval_v1.yaml`，共 **44 条**（数据设计 §九写的是「约 40」，按桶配比取 44）：

| 桶 | 内容 | 条数 | 主要指标 |
|---|---|---|---|
| B1 知识问答 | 政策、规格、活动、会员 | 10 | recall@k、要点覆盖、faithfulness |
| B2 实时数据 | 订单、物流、库存 | 8 | 字段准确率、查不到处理 |
| B3 规则计算 | 退款资格、金额 | 8 | 金额准确率、资格判定 |
| B4 边界条件 | 已发货、超 7 天、赠品、激活贬值 | 6 | 边界判定准确率 |
| B5 异常数据 | 缺字段、记录冲突、日期倒挂 | 4 | 守卫触发准确率 |
| B6 对抗输入 | 类型填错、问候+业务、越权 | 4 | 路由 / 越权拦截准确率 |
| B7 应转人工 | 红线、超权限、资金异常 | 4 | 转人工召回率、误放率 |

**复用现有资产**：

- B1 从 `knowledge_v1.yaml` 选 10 条查询，保留 formal / colloquial；
- B3 的简单案例参考 `refund_gold_20.json`，时间敏感案例用评测专用订单；
- 其余桶新写。

### 2.1.1 评测专用订单

`scripts/seed.py` 用的是固定 `NOW = 2026-10-28`，而退款规则按**运行时真实时间**
算 7 天窗口。隔几天再跑，「签收 2 天」就变成了「签收 10 天」。

所以评测库在 `seed.py` 之后加跑 `scripts/seed_eval.py`，插入 8 笔 u1 订单
（时间相对运行时刻生成）：

| 订单 | 用途 |
|---|---|
| 1101 | 签收 2 天，可退 |
| 1102 | 2 件，部分退款 |
| 1103 | 定制商品，第六条不适用 |
| 1104 | 奶瓶且已拆封，第七条不适用 |
| 1105 | 签收早于发货，日期倒挂 |
| 1106 | 签收 10 天，超 7 天 |
| 1107 | 签收 10 天但有质量问题，窗口例外 |
| 1108 | 已发货的定制商品，拒收退款优先 |

**只进评测库**：演示库不跑这个脚本，避免污染 seed 的可复现时间线。

### 2.2 单条结构

```yaml
version: v1
items:
  - id: K01
    bucket: B1
    query: "退货政策是什么"
    expect_route: knowledge
    expect_ids: ["returns#七日无理由退货的适用范围"]
    expected_output: "七日|7 个自然日/签收次日"
    should_refuse: false
    style: formal
    split: dev          # dev = 已用于调 prompt；test = 盲测，不参与调参
    notes: ""

  - id: D01
    bucket: B2
    query: "订单1001到哪了"
    expect_route: data
    expect_fields:
      order_id: "1001"
      status: "已签收"
      tracking_no: "SF1001001"
    expected_output: "已签收/SF1001001"
    split: test
```

### 2.3 要点语法

`expected_output` 用 `/` 分隔**必须出现的要点组**，组内用 `|` 表示**任选其一**：

```
"不可叠加|不能叠加|只能使用一张/会员折扣"
```

含义：两个要点组都必须命中，第一个组出现任一说法即可。

**为什么不用纯字面**：同一意思有多种说法，字面匹配会把正确答案判成缺失。
EvalFlow 现有 `p in text` 不支持 `|`，Ch07 做一次向后兼容的小扩展（见 §4.3）。

### 2.4 dev / test 与盲测

- 已经用来改过 prompt 的样本标 `dev`；
- 新写、未参与调参的标 `test`；
- **`test` 只跑不改**：run-01 后如果发现失败，只能改代码/数据，不能只针对该题改 prompt；
- 如果某条 `test` 确实参与过修复，必须移入 `eval_v1_dev_archive.yaml`（split=dev），
  再从候选池补同桶的新 test；不能“改完继续当 test”；
- 退款困难组（P7-8）单独维护，见 §5.3。

### 2.5 只做单轮

`eval_v1` 的每条只有一个 `query`。多轮能力（park、指代、槽位填充）继续由
Ch05 的场景测试和手工演示覆盖，**不塞进 EvalFlow**：

- EvalFlow 的 dataset item 只有 `input: str`，表达多轮要改数据模型；
- 多轮失败往往要回放整段对话，当前平台没有这个视图；
- 单轮集合先保证路由、数据、规则、转人工四条主线可量化。

---

## 三、评测专用门面 `/api/eval/chat`

### 3.1 契约

`POST /api/eval/chat`（非流式）：

```json
// 请求
{"message": "订单1001到哪了"}

// 响应 200
{
  "conversation_id": 123,
  "route": "data",
  "intent": "物流查询",
  "answer": "订单 1001 当前状态：已签收……",
  "references": [
    {"id": "returns#...", "text": "……", "source": "returns", "section": "..."}
  ],
  "hits": [
    {"id": "returns#...", "text": "……", "rank": 1, "score": 0.83, "cited": true},
    {"id": "returns#...", "text": "……", "rank": 2, "score": 0.71, "cited": false}
  ],
  "actions": [],
  "violations": [],
  "signals": {
    "tool_code": "ok",
    "pending_question": null,
    "refund": {"eligible": true, "reason_code": "size", "confidence": 0.92, "amount": 50000}
  }
}
```

| 字段 | 来源 | 用途 |
|---|---|---|
| `answer` | `final["answer"]`（**guard 之后**） | EvalFlow `output` |
| `references` | `final["evidence"]` 里被引用的条目 | `retrieved_context` |
| `hits` | `final["evidence"]` 完整 top-k | hit@k / MRR |
| `route` / `intent` | 图终态 | 分桶与路由准确率 |
| `actions` | 图终态 + guard 追加 | 转人工召回 |
| `violations` | `output_guard` | 输出检查统计 |
| `signals` | 图终态的允许字段 | 业务硬指标（金额/资格/追问） |

**非 knowledge 出口**：`references` / `hits` 为空数组。

**`signals` 只放白名单字段**，不返回完整 state：
不放订单快照、不放用户信息、不放 prompt / 证据全文以外的内部字段。
端点默认关闭，见 §3.3。

### 3.2 实现

复用 `run_turn`，不复制图逻辑：

```python
# chat.py
async def run_turn(..., capture: dict | None = None):
    ...
    if capture is not None:
        capture["final"] = final      # 图跑完、guard 之后
```

```python
# api/eval.py
captured: dict = {}
frames = [f async for f in run_turn(req.message, None, trace_id, capture=captured)]
final = captured["final"]
answer = "".join(f["delta"] for f in frames if "delta" in f)
evidence = final.get("evidence") or []
cited = set(final.get("citation_ids") or [])
hits = [
    {"id": e["id"], "text": e["text"], "rank": i + 1,
     "score": e.get("rrf_score"), "cited": e["id"] in cited}
    for i, e in enumerate(evidence)
]
```

- 每次请求 `conversation_id=None`，新建会话，题与题之间不共享记忆；
- 复用会话锁、窗口、输出检查、消息落库；
- 不改 `/api/chat` 的帧结构，前端不受影响。

### 3.3 开关与隔离

| 项 | 做法 |
|---|---|
| 端点开关 | `ENABLE_EVAL_API=true` 才响应；否则 404（不是 403） |
| 绑定地址 | 评测实例只绑 `127.0.0.1:8081` |
| 数据库 | `DATABASE_URL=sqlite+aiosqlite:///./data/eval.db`，不碰演示库 |
| 实例 | 不加 `--reload`；评测期间不重启 |
| 鉴权 | Ch07 不做；生产不开启该开关 |

**端口分配**：

| 服务 | 端口 |
|---|---|
| Chroma | 8000 |
| EvalFlow | 8002 |
| ecom 评测实例 | **8081** |
| ecom 日常开发 | 8080（`--reload`，不用于评测） |

### 3.4 错误语义

| 情况 | HTTP | 响应 |
|---|---|---|
| `invalid_arg` | 422 | `{"code": "invalid_arg", "message": ...}` |
| 会话不存在 | 404 | `{"code": "not_found"}`（评测不传 id，理论到不了） |
| 超时 | 504 | `{"code": "timeout"}` |
| 其他内部错误 | 500 | `{"code": "internal_error"}`，细节只进日志 |

EvalFlow 适配器对 4xx **不重试**（样本失败）；对 5xx / 超时最多重试 2 次。

---

## 四、EvalFlow 接入

### 4.1 配置

```bash
EVAL_TARGET=ecom
ECOM_BASE_URL=http://127.0.0.1:8081
ECOM_TIMEOUT=120
ECOM_MAX_RPM=30
```

### 4.2 适配器

`backend/external_ecom.py`：

```python
def run_pipeline_ecom(question: str, top_k: int = 2, retrieval: str = "hybrid") -> dict:
    d = _post("/api/eval/chat", {"message": question})
    return {
        "output": d["answer"],
        "retrieved_context": "\n".join(x["text"] for x in d["references"]),
        "hits": d["hits"],
    }

def fetch_meta_ecom() -> dict | None:
    return _get("/api/meta")     # 失败返回 None，快照标 meta_error
```

- `top_k` / `retrieval` 对 ecom **不生效**：被测系统内部策略是黑盒；
- 自我限流（`ECOM_MAX_RPM`），避免评测并发打爆本地模型 / Chroma；
- `run_service.py` 增加 `elif settings.eval_target == "ecom"`；
- `snapshot.py` 按 target 分发 `fetch_meta`。

### 4.3 EvalFlow 侧两处小改（向后兼容）

**① 无证据样本不评上下文指标。**

B2–B7 的出口没有检索证据。现在 `recall` 会记 0，
`faithfulness` / `context_*` 会拿 `(无证据)` 去问 judge——都是错的。

改为：

- `retrieved_context` 为空或 `(无证据)` 时，`recall` / `hit_at_k` / `mrr` 记 **NULL**；
- 跳过 `faithfulness` / `context_precision` / `context_recall`；
- `answer_relevance` / `answer_correctness` 照常跑。

**② 要点支持 `|` 任选。**

`recall` / `hit_at_k` / `mrr` 的匹配函数从 `p in text` 改成：

```python
def point_hit(point: str, text: str) -> bool:
    return any(alt.strip() in text for alt in point.split("|") if alt.strip())
```

不含 `|` 的旧数据行为不变。

> 这是 Ch07 唯一跨仓改动，需在 EvalFlow 的改动记录里单独登记。

### 4.4 数据集导入

ecom 侧 `scripts/export_evalflow.py`：

- 读 `data/eval/eval_v1.yaml`；
- 生成 `data/eval/evalflow_v1.json`（`input` / `expected_output` / `bucket`）；
- 通过 EvalFlow `POST /datasets` + `POST /datasets/{id}/items` 导入；
- 数据集名 `ecom-eval-v1`；重复导入按 `input` 去重，不重复建集。

### 4.5 run 快照

EvalFlow 的 `build_snapshot` 记录：

- 数据集哈希；
- `top_k` / `retrieval`；
- 被测系统 `/api/meta`：`kb_version`、`chat_model`、`prompt_version`、`app_env`；
- 拿不到 meta → `meta_error`，报告显式写「本次结果不可与历史对比」。

---

## 五、指标与门槛

### 5.1 指标分工

| 指标 | 谁算 | 用途 |
|---|---|---|
| `answer_correctness` / `answer_relevance` | EvalFlow judge | 答案质量 |
| `faithfulness` / `context_*` | EvalFlow judge（仅 B1） | 知识出口的忠实度 |
| `recall` / `hit_at_k` / `mrr` | EvalFlow 机械指标（仅 B1） | 检索质量 |
| 路由准确率 | `scripts/eval_e2e.py` | B2–B7 的主指标 |
| 字段准确率 | `scripts/eval_e2e.py` 读 `signals` / `answer` | B2 |
| 金额准确率 / 资格判定 | `scripts/eval_e2e.py` 读 `signals.refund` | B3 / B4 |
| 转人工召回 / 误放 | `scripts/eval_e2e.py` 读 `route` / `actions` | B7 |
| 延迟 / token / 成本 | EvalFlow trace | 性能预算 |
| 质量传导 2×2 | `scripts/eval_knowledge.py` | 检索→答案的传导 |
| 阈值曲线 | `scripts/eval_gate.py` | 误放 / 误拦 |
| 检索选型复核 | `scripts/eval_retrieval.py --set hard` | 稀有词上的 dense / hybrid / bm25 |

**为什么业务指标不交给 judge**：金额和路由是硬约束，judge 的方差会让回归门禁失去意义。

### 5.2 门槛（provisional）

run-01 先把基线打出来；以下数值是**初始假设**，run-02 前按基线校准。

| 指标 | 门槛 | 类型 |
|---|---|---|
| B2 字段准确率 | 1.00（模板回答） | 硬 |
| B3 金额准确率 | 1.00（规则覆盖范围内） | 硬 |
| B7 转人工召回 | 1.00 | 硬 |
| 越权三路径 | 全部 `not_found` | 硬（Ch06 回归） |
| 路由准确率 | ≥ 0.90 | 软 |
| B1 context_recall | ≥ 0.80 | 软 |
| B1 faithfulness | ≥ 0.80 | 软 |
| B1 answer_correctness | ≥ 0.80 | 软 |
| 误放率（该转人工却直接答） | ≤ 0.05 | 软 |
| p95 延迟（A/B/C） | ≤ 6s | 软 |
| p95 延迟（E） | ≤ 15s | 软 |

**硬门槛失败 = run FAIL**；软门槛失败只记录，不阻塞，但要在报告里写原因。

### 5.3 退款困难组（P7-8）

- 现有 `refund_gold_hard_10.json` 转为 **dev set**（它已经参与过两次 prompt 调整）；
- 新增 10–20 条，按失败类型分层：规则顺序 / 字段粒度 / 数据缺口 / 语义模糊 / 多轮补充；
- 新增部分**只做盲测**，不参与 prompt 调参；
- 报告给出 Wilson 95% CI 与 per-class 准确率；
- 目标 n ≥ 20，每类 ≥ 3。

**为什么不在 Ch03 直接加题**：旧的 10 条已经用来改 prompt，事实上是 dev set；
继续在上面加题调参，只是在更大的训练集上自测。

**落地（2026-11-09）**：

- dev：`refund_gold_hard_10.json`（10 条，`_split=dev`）；
- test：`refund_hard_test.json`（15 条，`_split=test`，5 类 × 3）；
- 合计 25 条，满足 n ≥ 20、每类 ≥ 3；
- 评测脚本修正：judge 必须使用 case 的 `now`，否则固定日期的 gold set
  会随运行当天漂移；修正后 Ch03 的 7/10 不可复现。
- 修正后先得到 dev 5/10；据此做了一次 **只依据 dev** 的 judge prompt 修复
  （数据缺口转人工、状态优先于类别、按件判定、非退款诉求）。
- 验收时发现 gold 冲突：主评测 B07 / seed 1108 规定“已发货未签收的定作商品
  → `reject_then_refund`”，H06/T01 原标 `not_applicable`。以规则表为准修正
  H06/T01/T03，规则顺序组降为**参照组**。
- 结果：dev **10/10**、held-out test **15/15**（95% CI 80–100%）；其中
  规则顺序参照组 3/3，其余 4 组 12/12。gold 修正后 test 三次均为 15/15。
- **held-out 边界**：prompt 只依据 dev 设计，但运行后修正了 T01/T03 的 gold，
  且修改前已看过一次 test 结果，因此只能称 held-out 验证，不是严格盲测；
  后续再改 prompt 或 gold 必须换新样本。报告必须同时写 CI 与运行次数。

### 5.4 质量传导（P7-7）

同批知识样本上列 2×2：

|                | 答案覆盖全部要点 | 答案缺要点 |
|----------------|------------------|------------|
| **检索命中**    | 正常             | 检索给了但没用上 |
| **检索未命中**  | 🚨 可疑          | 兜底，符合预期 |

第四格必须人工核验 ≥ 3 条：要么模型蒙对，要么编造。
这是 Ch06 输出检查的依据，也是 Ch07 报告里最有信息量的一节。

**命中口径**：同时看 top3（生产口径）与 top1（严格口径）。
top1 不是 gold、但 gold 在 top2/3 时，答案仍有依据，不能算编造。

**自然样本填不满四格时**（例如 hit@3 已达 100%），用两组受控降级补齐，
并在报告里标明每格的实验条件，不跨条件合并计数：

| 条件 | 做法 | 补哪格 |
|---|---|---|
| 截断 gold | 命中保持不变，证据文本只留前 N 字 | 命中但缺要点 |
| 错误证据 | 只喂 rank-k 低相关块，绕过闸 | 未命中且缺要点 / 可疑格 |

受控条件只用于诊断，不是生产配置；生产仍是 top5 + 机械/语义闸。
「检索未命中但答案完整」的每条都要写明：答案依据来自哪一条证据，
还是模型自带知识——后者一律按编造处理。

### 5.5 检索选型复核（P2 遗留待办）

Ch02 的结论是「dense 已到 1.00，无法再区分策略，Ch07 需加难度」。
Ch07 用一份独立的困难集补测：

- 数据：`data/eval/retrieval_hard_v1.yaml`，25 题 / 35 条查询；
- 刻意覆盖稀有词：型号（ST-20 / CS-04 / FB-30）、法条号（第七/八/十/十二条）、
  地区（西藏 / 内蒙古 / 港澳台）、发票类型（专票 / 抬头重开）；
- 指标：hit@3 + Wilson 95% CI + MRR + 相对 dense 的不一致对数；
- 决策规则：hybrid 只有出现**统计显著**优势才切换；否则维持 dense。

结论（2026-11-09）：dense 1.00 / hybrid 1.00 / bm25 0.97，hybrid 仍 0:0 不一致，
**维持 dense**。报告见 `docs/reports/retrieval-eval-hard.md`。

---

## 六、测试

| # | 测试 | 断言 |
|---|---|---|
| 1 | 评测集校验 | 44 条、7 桶、id 唯一、`expected_output` 非空、`split` 合法 |
| 2 | `/api/eval/chat` 正常 | 返回 answer / references / hits / signals；hits 有序 |
| 3 | `/api/eval/chat` 关闭 | `ENABLE_EVAL_API` 未设置 → 404 |
| 4 | 证据为空 | B/C/E 样本 `references` / `hits` 为空数组，不报错 |
| 5 | 错误映射 | 超时 504、内部错误 500、参数 422 |
| 6 | 会话隔离 | 两次调用 `conversation_id` 不同，无跨题记忆 |
| 7 | 演示接口不变 | `/api/chat` 帧顺序与 Ch06 一致 |
| 8 | 导出脚本 | YAML → JSON 条数一致、桶计数一致、去重生效 |
| 9 | EvalFlow 适配器 | 假 HTTP 返回映射为 output / context / hits；5xx 重试、4xx 不重试 |
| 10 | EvalFlow 无证据 | 上下文指标记 NULL，不记 0 |
| 11 | EvalFlow `\|` 语法 | `a\|b` 任一命中即算；旧数据行为不变 |
| 12 | 快照 | meta 可达时记录 kb_version；不可达时标 `meta_error` |

---

## 七、本章不做

| 不做 | 原因 / 触发条件 |
|---|---|
| 多轮端到端评测 | EvalFlow 数据模型只有单条 input；多轮由 Ch05 场景覆盖 |
| 真实鉴权 / 公网开放评测门面 | 评测实例只绑 localhost；生产不开启 |
| CI 定时评测 | 有稳定基线后再接 |
| 100+ 条评测集 | 语料只有 41 个 chunk，样本/语料比过高会过拟合 |
| 自己写 judge | EvalFlow 已有 5 个 judge 与校准 |
| 用 judge 判金额/路由 | 硬约束用确定性脚本 |
| 替换现有 `eval_*.py` | 层内诊断仍然需要 |
| 手机号 / 地址脱敏 | 仍用合成数据；接入真实数据时做 |

---

## 八、关键决策记录

| # | 决策 | 选择 | 放弃的方案 | 理由 |
|---|---|---|---|---|
| C7-1 | 评测接入方式 | **ecom 加非流式评测门面** | EvalFlow 解析 SSE | 主接口不为评测改形状；证据与 signals 一次拿全 |
| C7-2 | 评测层级 | **层内脚本 + 端到端 EvalFlow** | 只保留一种 | 失败要能归因到检索 / 生成 / 路由 |
| C7-3 | 评测集规模 | **44 条，7 桶** | 严格 40 / 100+ | 按数据设计配比；语料小，扩大会过拟合 |
| C7-4 | 多轮 | **单轮评测** | EvalFlow 改数据模型 | 成本高；Ch05 已有场景覆盖 |
| C7-5 | 评测端点 | **`ENABLE_EVAL_API` + 独立库 + 8081** | 直接挂在主实例 | 不污染演示数据；可随时关闭 |
| C7-6 | 证据契约 | **`references` 被引用 + `hits` 全量 top-k** | 只给 citations | hit@k / MRR 需要未引用的候选 |
| C7-7 | 无证据指标 | **记 NULL，不记 0** | 记 0 | B/C/E 没有检索，0 分是错的 |
| C7-8 | 要点语法 | **`/` 必选 + `\|` 任选** | 纯字面 | 同义说法多，字面会误判 |
| C7-9 | 盲测 | **`split=test` 只跑不改** | 全量调参 | 避免在测试集上自测 |
| C7-10 | 业务指标 | **本地确定性脚本** | LLM judge | 金额/路由是硬约束 |
| C7-11 | 快照 | **必须记录 `kb_version` 与外部 meta** | 只记自己这边 | 否则「变好」无法归因 |
| C7-12 | 门槛 | **先基线，后校准** | 拍一个百分比 | 没有基线就没有回归门禁 |

---

## 九、交付物

| 文件 | 内容 |
|---|---|
| `data/eval/eval_v1.yaml` | 44 条评测集（唯一真源） |
| `data/eval/eval_v1_dev_archive.yaml` | 参与过调参、不能再当 test 的 dev 样本 |
| `data/eval/evalflow_v1.json` | 导出给 EvalFlow 的扁平格式 |
| `scripts/seed_eval.py` | 评测专用订单（时间相对运行时刻） |
| `app/api/eval.py` | `/api/eval/chat` |
| `scripts/eval_e2e.py` | 路由 / 字段 / 金额 / 转人工的确定性指标 |
| `scripts/export_evalflow.py` | YAML → JSON + 导入 EvalFlow |
| `data/eval/refund_hard_test.json` | ≥ 20 条退款困难组（盲测） |
| `docs/reports/run-01.md` | 第一轮：基线、失败归因 |
| `docs/reports/run-02.md` | 第二轮：修了什么、指标变化 |
| `docs/reports/quality-propagation.md` | 2×2 与人工核验 |
| `docs/reports/refund-hard.md` | CI、per-class、盲测结论 |

EvalFlow 仓：

- `backend/external_ecom.py`
- `config.py` / `run_service.py` / `snapshot.py` 的 target 分支
- `evaluators.py`：`|` 任选 + 无证据 NULL
- 对应测试与改动记录

---

## 十、风险

| 风险 | 应对 |
|---|---|
| 44 条仍可能过拟合 41 个 chunk | dev/test 分离；test 只跑不改 |
| judge 方差让门禁抖动 | 硬指标不用 judge；软指标看 CI |
| 评测门面与 `/api/chat` 行为漂移 | 两者共用 `run_turn`；契约测试对照帧与 JSON |
| 评测实例用了旧 KB | run 快照记录 `kb_version`；启动前重建/校验 |
| 并发打爆本地模型 / Chroma | 适配器自我限流 `ECOM_MAX_RPM` |
| EvalFlow 跨仓改动影响 baby-ai | 两处改动向后兼容；旧数据集回归测试 |
| 评测库积累大量会话 | 独立 `data/eval.db`；必要时重置脚本 |
| `signals` 泄露内部状态 | 只返回白名单字段；端点默认关闭 |
