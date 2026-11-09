# Ch07 评测接入 实施计划

> **For agentic workers:** 按 task 顺序执行。步骤用 `- [ ]` 追踪。
> 每个 task 完成、用户确认后再 commit。

**Goal:** 用 44 条评测集跑通「层内诊断 + 端到端 EvalFlow」两条线，
产出 run-01 / run-02、阈值曲线、质量传导、退款困难组。

**Architecture:** ecom 新增非流式评测门面 `/api/eval/chat`；
EvalFlow 通过 `external_ecom.py` 消费它；业务硬指标由本地确定性脚本计算。

**Tech Stack:** 沿用 Ch01–Ch06；ecom 侧不引入新依赖；EvalFlow 沿用 FastAPI + Celery。

**Spec:** [ch07-eval-design.md](../specs/ch07-eval-design.md)
**前置:** Ch01–Ch06 已验收；EvalFlow 可本地启动；Chroma + Ollama 可用

---

## Global Constraints

- **不改 `/api/chat` 的帧结构**：前端和 SSE 行为保持不变。
- **评测端点默认关闭**：`ENABLE_EVAL_API` 未开启时返回 404。
- **评测库独立**：`DATABASE_URL=.../data/eval.db`，不碰演示库。
- **业务硬指标不用 judge**：金额、路由、转人工由确定性脚本判。
- **`split=test` 只跑不改**：盲测集发现失败，只能改代码/数据，不能针对题目改 prompt。
- **跨仓改动向后兼容**：EvalFlow 的 baby-ai 路径与旧数据集回归必须保持通过。
- **不引入新依赖**（ecom 侧；EvalFlow 的 `requests` 已有）。

---

### Task 1: 评测集 + 校验 + 导出（P7-1/P7-2/P7-3）

**Files:**
- Create: `data/eval/eval_v1.yaml`
- Create: `data/eval/eval_v1_dev_archive.yaml`（参与过调参的 dev 样本）
- Create: `app/core/eval_v1.py`
- Create: `scripts/seed_eval.py`
- Create: `scripts/export_evalflow.py`
- Create: `scripts/generate_eval_candidates.py`（外部模型生成候选）
- Create: `scripts/import_generated_candidates.py`（网页模型手工输出导入）
- Create: `scripts/build_candidate_review.py`（候选评审表）
- Create: `scripts/shortlist_candidates.py`（58 条短名单）
- Create: `scripts/review_candidates.py`（终端逐条审，可中断续审）
- Create: `data/eval/generation/`（事实卡 / prompts / raw / manual / review）
- Create: `tests/eval/test_eval_v1.py`、`tests/eval/test_generate_candidates.py`、
  `tests/eval/test_import_generated.py`、`tests/eval/test_build_candidate_review.py`、
  `tests/eval/test_shortlist_candidates.py`、`tests/eval/test_review_candidates.py`
- Verify: `app/core/kb_version.py`（已存在，不改）

**Step 1: 定义单条结构**

```yaml
version: v1
items:
  - id: K01
    bucket: B1
    query: "退货政策是什么"
    expect_route: knowledge
    expect_ids: ["returns#七日无理由退货的适用范围"]
    expected_output: "七日|7 ?个?自然日/签收次日"
    should_refuse: false
    style: formal
    split: dev
    notes: ""
```

B2–B7 额外字段：

```yaml
    expect_fields: {order_id: "1001", status: "已签收"}
    expect_refund: {eligible: true, amount: 50000}
    expect_actions: ["transfer_human"]
```

**Step 2: 写 44 条**

| 桶 | 条数 | 来源 |
|---|---|---|
| B1 知识 | 10 | 从 `knowledge_v1.yaml` 选 |
| B2 数据 | 8 | 新写：订单 / 物流 / 库存 / 查不到 |
| B3 规则 | 8 | 参考 `refund_gold_20.json`，时间敏感项用 11xx 订单 |
| B4 边界 | 6 | 定制 / 拆封奶瓶 / 超 7 天 / 已发货 |
| B5 异常 | 4 | 日期倒挂 / 缺物流记录 / 缺订单 / 缺单号 |
| B6 对抗 | 4 | 类型错 / 问候+业务 / 越权 / 注入 |
| B7 转人工 | 4 | 投诉 3 条 + 越权退款 1 条 |

**评测专用订单**：`scripts/seed_eval.py` 插入 1101–1108，
时间相对运行时刻生成，避免 `seed.py` 固定 NOW 让 7 天窗口过期。

**Step 3: loader**

`app/core/eval_v1.py`：

```python
@dataclass(frozen=True)
class EvalItem:
    id: str
    bucket: str
    query: str
    expect_route: str
    expected_output: str
    expect_ids: list[str]
    expect_fields: dict
    expect_refund: dict | None
    expect_actions: list[str]
    should_refuse: bool
    style: str
    split: str

def load_items(path=DEFAULT) -> list[EvalItem]
```

**Step 4: 校验测试**

- 44 条，桶计数 `10/8/8/6/4/4/4`；
- id 唯一；
- `expect_route ∈ {knowledge,data,rules,human,fallback}`；
- `split ∈ {dev,test}`，`test` 至少 14 条；
- `expected_output` 非空，`/` 与 `|` 语法可解析；
- `should_refuse=True` 时 `expect_route=human` 或 `knowledge`（由实现定）；
- `kb_version` 非空：`uv run python -c "from app.core.kb_version import cached_kb_version; print(cached_kb_version())"`。

**Step 5: 导出**

`scripts/export_evalflow.py`：

- 读 YAML → `data/eval/evalflow_v1.json`；
- 字段：`input` / `expected_output` / `bucket`；
- `--import` 时 POST 到 EvalFlow `:8002`，数据集名 `ecom-eval-v1`，按 `input` 去重。

**测试：**
- loader 与校验测试全绿；
- 导出条数与桶计数一致；
- 重复导出不产生重复 item。

**Commit:** `feat(eval): 44 条评测集与 EvalFlow 导出`

---

### Task 2: 评测门面 `/api/eval/chat`

**Files:**
- Modify: `app/config.py`、`app/api/chat.py`、`app/main.py`
- Create: `app/api/eval.py`
- Create: `tests/api/test_eval_api.py`

**Step 1: 配置开关**

```python
enable_eval_api: bool = False
```

**Step 2: `run_turn` 采集器**

```python
async def run_turn(..., capture: dict | None = None):
    ...
    if capture is not None:
        capture["final"] = final     # guard 之后
```

只增加可选参数，现有调用不受影响。

**Step 3: 路由**

`app/api/eval.py`：

```python
router = APIRouter()

class EvalRequest(BaseModel):
    message: str

@router.post("/api/eval/chat")
async def eval_chat(req: EvalRequest) -> dict:
    if not get_settings().enable_eval_api:
        raise HTTPException(status_code=404, detail="Not Found")
    ...
```

响应字段按 spec §3.1：

- `answer`：拼 `delta`，guard 之后；
- `references`：被引用的 evidence；
- `hits`：完整 top-k，带 `rank` / `score` / `cited`；
- `route` / `intent` / `actions` / `violations`；
- `signals` 白名单：`tool_code` / `pending_question` / `refund`。

**Step 4: 注册**

`app/main.py`：

```python
from app.api.eval import router as eval_router
app.include_router(eval_router)
```

开关在请求内判断，不在 import 时判断——测试可以 monkeypatch。

**Step 5: 错误映射**

| 异常 | HTTP |
|---|---|
| `ChatError("invalid_arg")` | 422 |
| `ChatError("timeout")` | 504 |
| 其他异常 | 500 |
| 开关关闭 | 404 |

**测试：**
- 开关开：正常返回；`hits` 有序；`cited` 标记正确；
- B/C/E 样本：`references` / `hits` 为空；
- 开关关：404；
- 超时 / 内部错误映射；
- 两次调用 `conversation_id` 不同；
- `/api/chat` 帧顺序回归不变。

**Commit:** `feat(eval): 评测门面 /api/eval/chat`

---

### Task 3: 确定性端到端指标

**Files:**
- Create: `scripts/eval_e2e.py`
- Create: `tests/eval/test_eval_e2e.py`

**Step 1: 指标**

`scripts/eval_e2e.py --base-url http://127.0.0.1:8081`：

| 指标 | 判法 |
|---|---|
| 路由准确率 | `resp.route == expect_route` |
| 字段准确率（B2） | 每个 `expect_fields` 值出现在 `answer` 或 `signals` |
| 金额 / 资格（B3） | `signals.refund.amount == expect_refund.amount`；`eligible` 相等 |
| 转人工召回（B7） | `transfer_human` 出现在 `actions` |
| 误放率（B7） | 应转人工却直接给出结论 |
| 拒答正确率 | `should_refuse` 与 `route/actions` 一致 |

**Step 2: 输出**

- 控制台摘要；
- `--out` 写 markdown：总表 + 分桶 + 失败样本清单；
- 每条失败带 `id` / `query` / 期望 / 实际。

**Step 3: 测试**

用 fake `httpx` 响应覆盖：

- 全对 → 1.00；
- 一条路由错 → 计入失败；
- 缺少 `signals` 不崩，按「无法判定」计数；
- B7 漏转人工 → 误放。

**Commit:** `feat(eval): 端到端确定性指标脚本`

---

### Task 4: EvalFlow 适配器与快照

**Files (EvalFlow 仓):**
- Create: `backend/external_ecom.py`
- Modify: `backend/config.py`、`backend/run_service.py`、`backend/snapshot.py`、`.env.example`
- Create: `tests/test_external_ecom.py`

**Step 1: 配置**

```python
ecom_base_url: str = "http://127.0.0.1:8081"
ecom_timeout: int = 120
ecom_max_rpm: int = 30
```

**Step 2: 适配器**

- `run_pipeline_ecom(question, top_k, retrieval)`：
  - POST `/api/eval/chat`；
  - `output = answer`；
  - `retrieved_context = "\n".join(references.text)`；
  - `hits` 原样透传；
- 自我限流；
- 5xx / 超时重试 2 次；4xx 不重试；
- `fetch_meta_ecom()`：GET `/api/meta`，失败返回 None。

**Step 3: 注册**

```python
elif settings.eval_target == "ecom":
    from backend.external_ecom import run_pipeline_ecom
    run_pipeline = run_pipeline_ecom
```

**Step 4: 快照**

`_external_snapshot` 按 target 分发 babyai / ecom；
拿不到 meta 时写 `meta_error`，不静默记空。

**测试：**
- 假 HTTP：响应映射正确；
- 5xx 重试 2 次；4xx 不重试；
- meta 不可达 → `None`；
- baby-ai 既有测试全绿。

**Commit:** `feat(eval): EvalFlow 接入 ecom 外部目标`

---

### Task 5: EvalFlow 指标语义（向后兼容）

**Files (EvalFlow 仓):**
- Modify: `backend/evaluators.py`、`backend/run_service.py`
- Test: `tests/test_evaluators.py`、`tests/test_run_service.py`

**Step 1: 要点 `|` 任选**

```python
def point_hit(point: str, text: str) -> bool:
    return any(alt.strip() in text for alt in point.split("|") if alt.strip())
```

`recall` / `hit_at_k` / `mrr` 共用。

**Step 2: 无证据记 NULL**

- `has_context = retrieved_context.strip() not in {"", "(无证据)"}`；
- 无上下文：`recall` / `hit_at_k` / `mrr` 返回 `None`；
- 跳过 `context_precision` / `context_recall` / `faithfulness`；
- `answer_relevance` / `answer_correctness` 照常。

**Step 3: 兼容性**

- 不含 `|` 的旧数据行为不变；
- 有上下文的 baby-ai run 指标不变；
- `Score.value` 已可空，无迁移。

**测试：**
- `a|b` 任一命中为真；`a|b` 都不出现为假；
- 无上下文指标为 `None`，不是 0；
- 旧数据集回归。

**Commit:** `fix(eval): 无证据指标记 NULL，要点支持任选`

---

### Task 6: 导入数据集 + run-01（P7-4）

**Step 1: 起评测实例**

```bash
ENABLE_EVAL_API=true \
DATABASE_URL=sqlite+aiosqlite:///./data/eval.db \
uv run python scripts/init_db.py
ENABLE_EVAL_API=true \
DATABASE_URL=sqlite+aiosqlite:///./data/eval.db \
uv run python scripts/seed.py
ENABLE_EVAL_API=true \
DATABASE_URL=sqlite+aiosqlite:///./data/eval.db \
uv run python scripts/seed_eval.py
ENABLE_EVAL_API=true \
DATABASE_URL=sqlite+aiosqlite:///./data/eval.db \
uv run uvicorn app.main:app --port 8081       # 不加 --reload
```

**Step 2: 起 EvalFlow**

```bash
EVAL_TARGET=ecom ECOM_BASE_URL=http://127.0.0.1:8081 \
uv run uvicorn backend.main:app --port 8002
# 另开 worker，环境变量同上
```

**Step 3: 导入并跑**

```bash
uv run python scripts/export_evalflow.py --import --base-url http://localhost:8002
# EvalFlow UI / POST /runs 发起 run
```

**Step 4: 写 `docs/reports/run-01.md`**

必须包含：

- run 快照：数据集哈希、`kb_version`、模型、prompt 版本；
- 总表：每桶主指标 + 样本量；
- 失败样本按原因归类：路由 / 检索 / 生成 / 规则 / 数据 / 转人工；
- 每条失败能指到具体 `id`；
- 明确哪些是 `test` 集，未用于调参。

**Commit:** `test(eval): run-01 基线与失败归因`

---

### Task 7: 修复 + run-02（P7-5）

**Step 1: 选修复项**

只修 **run-01 中有归因的根因**，不按单题打补丁：

**已完成的前置修复（2026-11-09）**：

- 新增 `data/kb/faq.md`（15 条），解决「退货政策是什么」等宽泛问法的 top-5 召回；
  chunk 41→57，`kb_version=80bd26573951`；dense hit@3 1.00 / MRR 0.964，
  知识要点覆盖 40/40。详见改动记录 §8。
- 语义证据闸：机械三态 + 0.55–0.60 边界自评，3s 超时、不重试、fail-closed；
  域外探测 8/8 拦住，可答题 42/42 放行。详见改动记录 §9。

| 失败类型 | 允许的修法 |
|---|---|
| 路由错 | 补 intent 示例 / 路由规则 |
| 检索未命中 | 改语料或切分；不改 test 题 |
| 生成缺要点 | 改 prompt 的通用表述 |
| 规则/数据错 | 修代码或 seed |
| 该转人工未转 | 补闸 / 红线 |

**Step 2: 回归**

- 层内脚本先跑：`eval_knowledge` / `eval_refund` / `eval_gate`；
- 再跑 run-02；
- 指标变化写「改了什么 → 哪桶变化 → 为什么」。

**Step 3: `docs/reports/run-02.md`**

- 与 run-01 逐桶对比；
- 修复项与未修复项的清单；
- 明确 `test` 集没有参与调参；
- 若门槛未达标，写原因和下一步。

**Commit:** `test(eval): run-02 修复与对比`

---

### Task 8: 阈值扫描 + 质量传导（P7-6/P7-7）

**Files:**
- Modify: `scripts/eval_gate.py`（支持 `eval_v1` 与输出曲线）
- Modify: `scripts/eval_knowledge.py`（如需要支持 `eval_v1` 的 B1 子集）
- Create: `docs/reports/quality-propagation.md`
- Update: `docs/reports/gate-eval.md`

**Step 1: 阈值扫描**

- 扫描范围：0.40–0.75，步长 0.01；
- 输出：阈值 → 误放率 / 误拦率；
- 标注当前 0.55 与已知漏点「能分期付款吗」；
- 结论：是否调整阈值，或维持并记录缺口。

**Step 2: 质量传导 2×2**

- 同批 B1 样本；
- 四格都要有样本；
- 「检索未命中但答案完整」人工核验 ≥ 3 条；
- 判定是否编造，写进报告。

**Step 3: 报告**

`quality-propagation.md`：2×2 表、样本 id、人工核验记录、结论。
`gate-eval.md`：追加本轮曲线与决策。

**Commit:** `docs(eval): 阈值曲线与质量传导`

---

### Task 9: 退款困难组 + 评测方法论（P7-8）

**Files:**
- Create: `data/eval/refund_hard_test.json`
- Modify: `scripts/eval_refund.py`（Wilson CI + per-class）
- Create: `docs/reports/refund-hard.md`

**Step 1: 分层**

现有 10 条转 dev；新增 10–20 条：

| 类型 | 最少条数 |
|---|---|
| 规则顺序 | 3 |
| 字段粒度 | 3 |
| 数据缺口 | 3 |
| 语义模糊 | 3 |
| 多轮补充 | 3 |

**Step 2: 盲测纪律**

- 新增部分只跑不改；
- 报告写明「未参与 prompt 调整」；
- gold 由人工标注，标准沿用 Ch03 标注规则。

**Step 3: 统计**

- Wilson 95% CI；
- per-class 准确率；
- 模型 / 规则 / 流水线三列，别混着看；
- n ≥ 20。

**Commit:** `test(eval): 退款困难组与置信区间`

---

### Task 10: 验收 + 文档

**Files:**
- Create: `docs/reports/ch07-acceptance.md`
- Modify: `docs/superpowers/plans/00-tasks.md`
- Update: `docs/reports/00-change-log-overview.md`
- Create: `docs/reports/ch07-eval-changes.md`
- Optionally: `docs/interview/ch07-eval.md`（单独任务）

**Step 1: 验收清单**

- [ ] 44 条评测集校验通过
- [ ] `/api/eval/chat` 正常 / 关闭 / 错误映射
- [ ] EvalFlow 导入并跑完 run-01
- [ ] run-02 指标变化可解释
- [ ] 阈值曲线
- [ ] 质量传导 2×2
- [ ] 退款困难组 n ≥ 20 + CI
- [ ] `/api/chat` 回归不变
- [ ] 全量测试通过

**Step 2: 状态**

- P7-1 … P7-8 标 ✅；
- 改动记录索引 Ch07 → ✅；
- 写 `ch07-acceptance.md`（结论、清单、命令、已知边界）。

**Commit:** `docs(eval): Ch07 验收与文档`

---

## 与 spec / P7 的对应

| spec 项 | 落在哪个 Task |
|---|---|
| 44 条评测集 | 1 |
| 评测门面 | 2 |
| 确定性业务指标 | 3 |
| EvalFlow 适配 / 快照 | 4 |
| 无证据 NULL / `\|` 任选 | 5 |
| run-01 | 6 |
| run-02 | 7 |
| 阈值 / 质量传导 | 8 |
| 退款困难组 | 9 |
| 验收 | 10 |

| P7 任务 | Task |
|---|---|
| P7-1 / P7-2 / P7-3 | 1 |
| P7-4 | 6 |
| P7-5 | 7 |
| P7-6 / P7-7 | 8 |
| P7-8 | 9 |

---

## 风险

| 风险 | 应对 |
|---|---|
| 评测集过拟合 41 个 chunk | dev/test 分离；test 只跑不改 |
| 评测门面与 `/api/chat` 漂移 | 共用 `run_turn`；契约测试 |
| EvalFlow 跨仓改动破坏 baby-ai | 向后兼容 + 旧数据回归 |
| 评测实例用了旧 KB | 快照记 `kb_version`；启动前校验 |
| 并发打爆本地模型 | `ECOM_MAX_RPM` 自我限流 |
| judge 方差 | 硬指标不用 judge；软指标看 CI |
| `test` 集被无意调参 | 报告与脚本都按 `split` 过滤 |
| 评测库积累会话 | 独立 `data/eval.db`；必要时重置 |
