# Ch03 C 出口 实施计划

> **For agentic workers:** 按 task 顺序执行，每个 task 完成即 commit。步骤用 `- [ ]` 追踪。

**Goal:** 把 `rules` 出口从占位换成 9 状态退款流水线：**模型判资格 + 代码算金额 + 代码否决**。

**Architecture:** LangGraph 子图，9 状态 / 17 转移；模型出现在 `extract` / `judge` / `respond` 三个节点。

**Spec:** [ch03-rules-exit-design.md](../specs/ch03-rules-exit-design.md)
**规则来源:** [电商客服-规则表.md](../../design/电商客服-规则表.md)
**标注规则:** [电商客服-退款评测标注规则.md](../../design/电商客服-退款评测标注规则.md)

---

## Global Constraints

- **技术选型不变**：不加新依赖。
- **`compute_amount` 必须是纯函数**：不读时钟、不做 IO、不用随机。
- **金额只能由代码算**：模型不许输出数字。
- **模型与代码分歧 → 转人工**：不互相覆盖。
- **`confidence < 0.7` → 转人工**：拿不准不放行（fail-closed）。
- **归属校验在代码里**：这个子图不经过工具层。
- 每个 task 完成即 commit。

---

### Task 1: 数据变更（`signed_at` + `quantity`）

**Files:**
- Modify: `app/db/schema.sql`、`app/db/models.py`、`scripts/seed.py`

**Step 1: 加字段**

```python
signed_at: Mapped[datetime | None]   # 签收时间；NULL = 未签收
quantity: Mapped[int]                # 件数，默认 1
```

`schema.sql` 与 `models.py` 同步，`init_db.py` 的一致性比对会拦住漂移。

**Step 2: seed 填值**

- 「已签收 / 退款中」的单：`signed_at = shipped_at + 2 天`
- `quantity`：多数为 1，挑 3 单填 2（部分退款用例）

**验收**：`uv run python scripts/init_db.py` 通过；`sqlite3` 查得到新字段。

**Commit:** `feat(db): orders 增加 signed_at 与 quantity`

---

### Task 2: 规则表落地（供代码复算用）

**Files:**
- Create: `app/rules/__init__.py`
- Create: `app/rules/eligibility.py`
- Test: `tests/test_rules_eligibility.py`

**Interfaces:**

```python
def judge_eligibility(
    order: OrderSnapshot, request: RefundRequest, *, now: datetime
) -> EligibilityVerdict   # {eligible, reason_code}
```

**说明**：这套规则**不是最终判定**，而是给 `compliance` 做交叉校验用的独立实现。
模型判一遍、代码判一遍，不一致就转人工。

**Step 1: 按规则表 §三 的 10 条顺序短路实现**

**Step 2: 测试 10 条**

每条一正一反。

**验收**：函数无 `datetime.now()`、无 IO。

**Commit:** `feat(rules): 规则表判定（供复算）`

---

### Task 3: `compute_amount` 纯函数

**Files:**
- Create: `app/rules/refund.py`
- Test: `tests/test_rules_refund.py`

```python
def compute_amount(
    order: OrderSnapshot, judgement: Judgement, request: RefundRequest
) -> int
```

```
不可退 → 0
可退   → floor(实付 × 退货件数 / 总件数)
```

**测试**：整单退、部分退（2 退 1）、件数=总件数、不可退、件数越界（>总件数 → 按总件数封顶）。

**验收**：纯函数断言（无 `await`、无 `now()`）。

**Commit:** `feat(rules): 退款金额纯函数`

---

### Task 4: 子图骨架（9 状态 / 17 转移，全空实现）

**Files:**
- Create: `app/graph/rules_exit.py`
- Test: `tests/test_rules_graph.py`

**Step 1: 9 个节点全用 `_stub`**，边按 spec 转移表写；守卫各是一个函数。

**Step 2: 结构测试**

断言 9 个节点都在图里；每个守卫的返回值集合与 `add_conditional_edges` 的键一致。

**Commit:** `feat(rules): 9 状态子图骨架（空实现）`

---

### Task 5: `intake` 与 `need_info`

**Files:** Modify `app/graph/rules_exit.py`；Test `tests/test_rules_intake.py`

- `intake`：正则 `(?<!\d)(\d{4,})(?!\d)` 抽订单号
- `need_info`：缺单号 → `list_user_orders` 取最近 3 单 + `interrupt({"type": "select_order"})`

**验收**：三条测试（含单号直达、无线索列候选、resume 回填）。

**Commit:** `feat(rules): intake 抽单号 + need_info 列候选`

---

### Task 6: `extract`（模型抽要素）

**Files:** Modify `app/core/prompts.py`、`app/graph/rules_exit.py`；Test `tests/test_rules_extract.py`

```python
EXTRACT_SCHEMA = {
    "reason_code": ["quality", "no_reason", "other"],
    "item_count": int,
    "condition_ok": bool | None,   # 三态，拿不准给 null
}
```

**验收**：4 条样例抽取正确；"拿不准"时确实给 `null`。

**Commit:** `feat(rules): extract 结构化抽取`

---

### Task 7: `validate`

**Files:** Modify `app/graph/rules_exit.py`；Test `tests/test_rules_validate.py`

三个守卫：要素齐全、订单属于本人、日期不矛盾。`condition_ok` 缺失**不拦**（交给 judge）。

**验收**：每条一正一反。

**Commit:** `feat(rules): validate 守卫`

---

### Task 8: `query_data`（订单 + 政策）

**Files:** Modify `app/graph/rules_exit.py`；Test `tests/test_rules_query.py`

```python
order = await repo_order.get_order(order_id, user_id=...)
evidence = await retrieval.search(f"退款 {reason} {order.status}", top_k=5)
```

**失败分支**：订单 `NotFound` → need_info；**政策空** → 转人工。

**验收**：越权走 need_info；检索为空走转人工。

**Commit:** `feat(rules): query_data 取单 + 政策检索`

---

### Task 9: `judge`（模型判资格）

**Files:** Modify `app/core/prompts.py`、`app/graph/rules_exit.py`；Test `tests/test_rules_judge.py`

**Step 1: prompt**

给三样东西：**规则表（人话版）** + **政策原文（带编号）** + **订单快照**。
要求输出 `{eligible, reason_code, confidence}`，`reason_code` 是封闭集合。

**Step 2: 门槛**

`confidence < 0.7` → 转人工。

**Step 3: 测试（mock 模型）**

- 高分 → 进 compute
- 低分 → 转人工
- 格式错误 → 重试一次后转人工

**Commit:** `feat(rules): judge 模型判定资格`

---

### Task 10: `compliance`（红线 + 独立复算）

**Files:** Modify `app/graph/rules_exit.py`；Test `tests/test_rules_compliance.py`

**两件事**：

1. 四条红线（金额异常、法定不适用却可退、超期无理由却可退…）
2. **调用 `judge_eligibility` 复算**，与模型结果对比；不一致 → 转人工 + 记 trace

**验收**：四条红线各一例；分歧用例确实转人工；一致用例放行。

**Commit:** `feat(rules): compliance 红线与复算**

---

### Task 11: `respond` + 接入主图

**Files:** Modify `app/core/prompts.py`、`app/graph/rules_exit.py`、`app/graph/build.py`

- prompt 里给算好的金额，要求"直接引用，不要自己算"
- `g.add_node("rules", build_rules_graph())`（节点名不变）
- 最简机械校验：金额（元）必须逐字出现在回答里；完整版留 Ch06

**验收**：端到端 curl，金额与 `compute_amount` 一致。

**Commit:** `feat(rules): respond 复述金额 + 接入主图`

---

### Task 12: gold set 与手算对拍

**Files:** Create `data/eval/refund_gold_20.json`、`data/eval/refund_10.json`、`scripts/eval_refund.py`

**两批数据**：

| 文件 | 用途 |
|---|---|
| `refund_gold_20.json` | 20 单人工标注，测 `judge` 准确率（≥85%）与分歧率（<10%） |
| `refund_10.json` | 10 份合同，测 `compute_amount` 金额（10/10 一致） |

**"手算"的要求**：`expected` 由人工先算好写死，**不能先跑代码再抄**。

**验收**：`uv run python scripts/eval_refund.py` 输出两张表。

**Commit:** `test(rules): 判定 gold set 与金额对拍`

---

### Task 13: 验收

- [ ] `uv run pytest -q` 全绿
- [ ] `judge` 准确率 ≥ 85%；分歧率 < 10%
- [ ] 金额对拍 10/10
- [ ] 9 状态可达；17 条转移各有测试
- [ ] 端到端：命中转人工的四种情形各一条
- [ ] 更新 `00-tasks.md` P3 勾选
- [ ] 写 `docs/reports/ch03-acceptance.md`

**Commit:** `test: Ch03 验收（退款流水线 + 判定 + 对拍）`

---

## 与 spec 的对应

| spec 验收项 | 落在哪个 task |
|---|---|
| 9 状态可达 / 17 转移 | Task 4 / 13 |
| 判定准确率 ≥ 85% | Task 12 |
| 金额对拍 10/10 | Task 12 |
| 模型与代码分歧转人工 | Task 10 |
| 缺单号列候选 | Task 5 |
| 金额逐字一致 | Task 11 |

## 风险

| 风险 | 应对 |
|---|---|
| 模型判定不稳定 | `temperature=0`；gold set 重复跑两次看一致性 |
| 分歧率过高（>10%） | 说明规则表或 prompt 有问题，先修再上线 |
| `compute_amount` 混入时间/IO | 签名强制传 `now`；测试断言无 `now()` |
| `interrupt` 恢复时节点重跑 | `need_info` 之前只做只读 |
| 金额对拍"先跑代码再抄" | 合同先人工算；不一致先怀疑代码 |
