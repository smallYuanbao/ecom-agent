# Ch03 C 出口 实施计划

> **For agentic workers:** 按 task 顺序执行，每个 task 完成即 commit。步骤用 `- [ ]` 追踪。

**Goal:** 把 `rules` 出口从占位换成 8 状态退款流水线，金额由代码算、合规必须跑。

**Architecture:** LangGraph 子图，8 状态 / 15 转移；模型只在 `extract` 与 `respond` 出现。

**Spec:** [ch03-rules-exit-design.md](../specs/ch03-rules-exit-design.md)
**规则来源:** [电商客服-规则表.md](../../design/电商客服-规则表.md)

---

## Global Constraints

- **技术选型不变**：Ch01/Ch02 用过的栈，本章不加新依赖。
- **`compute` 必须是纯函数**：不读时钟、不做 IO、不用随机；当前时间由调用方传入。
- **模型只做两件事**：`extract` 抽要素、`respond` 组织语言。判定与计算一律代码。
- **fail-closed**：`condition_ok` 拿不准 → 转人工；两次判定不一致 → 转人工。
- **归属校验在代码里**：这个子图不经过工具层，工具那道校验够不着。
- 每个 task 完成即 commit。

---

### Task 1: 数据变更（`signed_at` + `quantity`）

**Files:**
- Modify: `app/db/schema.sql`、`app/db/models.py`
- Modify: `scripts/seed.py`
- Modify: `tests/test_models.py`（如需断言字段）

**Interfaces:**

```python
signed_at: Mapped[datetime | None]   # 签收时间；NULL = 未签收
quantity: Mapped[int]                # 件数，默认 1
```

**Step 1: 加字段**

`schema.sql` 与 `models.py` 同步加，`init_db.py` 的一致性比对会自动拦住漂移。

**Step 2: seed 填值**

- 状态为「已签收 / 退款中」的单：`signed_at = shipped_at + 2 天`
- `quantity`：大部分 1，挑 3 单填 2（用于部分退款用例）

**Step 3: 验收**

```bash
uv run python scripts/init_db.py        # schema.sql 与 ORM 一致
uv run python scripts/seed.py
sqlite3 data/app.db "select id, status, signed_at, quantity from orders limit 5;"
```

**Commit:** `feat(db): orders 增加 signed_at 与 quantity`

---

### Task 2: 规则表落地 + `compute_refund` 纯函数

**Files:**
- Create: `app/rules/__init__.py`
- Create: `app/rules/refund.py`
- Test: `tests/test_rules_refund.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class OrderSnapshot:
    order_id: str
    status: str
    amount: int              # 分
    quantity: int
    created_at: datetime
    signed_at: datetime | None
    category: str            # 商品类别，用于法定不适用判断

@dataclass(frozen=True)
class RefundRequest:
    reason_code: str         # quality / no_reason / other
    item_count: int
    condition_ok: bool | None

@dataclass(frozen=True)
class RefundResult:
    eligible: bool
    refund_amount: int       # 分；不可退时为 0
    reason_code: str         # 不可退的原因码
    breakdown: dict

def compute_refund(
    order: OrderSnapshot, request: RefundRequest, *, now: datetime
) -> RefundResult
```

**Step 1: 判定（规则表 §三 的 10 条，按顺序短路）**

**Step 2: 金额（规则表 §4.1）**

```python
refund_amount = order.amount * request.item_count // order.quantity
```

**Step 3: 测试 10 条**

规则表每条判定各一正一反；金额至少 4 例（整单、部分、件数=总件数、件数越界）。

**验收**：`pytest tests/test_rules_refund.py -q` 绿；**函数体内无 `datetime.now()`、无 `await`**。

**Commit:** `feat(rules): 退款规则与纯函数计算`

---

### Task 3: 子图骨架（8 状态 / 15 转移，全空实现）

**Files:**
- Create: `app/graph/rules_exit.py`
- Test: `tests/test_rules_graph.py`

**Interfaces:**

```python
def build_rules_graph() -> CompiledStateGraph
```

**Step 1: 节点全用 `_stub`，边按 spec 的转移表写**

守卫用独立函数（`_after_intake` / `_after_validate` / …），每个返回字符串键。

**Step 2: 结构测试**

断言 8 个节点都在图里；每个守卫函数的返回值集合与 `add_conditional_edges` 的键一致。

**验收**：图能编译；节点名与转移键对得上。

**Commit:** `feat(rules): 8 状态子图骨架（空实现）`

---

### Task 4: `intake` 与 `need_info`

**Files:**
- Modify: `app/graph/rules_exit.py`
- Test: `tests/test_rules_intake.py`

**Step 1: `intake`**

正则 `(?<!\d)(\d{4,})(?!\d)` 抽订单号（沿用参考实现的边界写法：CJK 与数字同属 `\w`，`\b` 不可靠）。

**Step 2: `need_info`**

缺 `order_id` → `list_user_orders` 取最近 3 单，`interrupt({"type": "select_order", "orders": ...})`。
缺其他字段 → 文本追问。

**Step 3: 测试**

- 原句含订单号 → 直接进 extract
- 无线索 → 返回候选列表（3 条）+ `interrupt` 结构正确
- 用户选单后 resume → 回填 `order_id`

**验收**：三条测试绿。

**Commit:** `feat(rules): intake 抽单号 + need_info 列候选`

---

### Task 5: `extract`（模型抽取）

**Files:**
- Modify: `app/core/prompts.py`（`REFUND_EXTRACT_*`）
- Modify: `app/graph/rules_exit.py`
- Test: `tests/test_rules_extract.py`

**Interfaces:**

```python
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "reason_code": {"type": "string", "enum": ["quality", "no_reason", "other"]},
        "item_count": {"type": "integer", "minimum": 1},
        "condition_ok": {"type": ["boolean", "null"]},   # 三态
    },
    "required": ["reason_code"],
}
```

**关键约束**：prompt 里明确「拿不准就给 null，不要猜」。`condition_ok=null` 会在 validate 阶段转人工。

**验收**：4 条样例（明确质量问题 / 明确无理由 / 拿不准完好 / 信息不足）抽取正确。

**Commit:** `feat(rules): extract 结构化抽取退款要素`

---

### Task 6: `validate`

**Files:**
- Modify: `app/graph/rules_exit.py`
- Test: `tests/test_rules_validate.py`

**Step 1: 四个守卫**

| 检查 | 失败去向 |
|---|---|
| `order_id`、`reason_code` 齐全 | need_info |
| 订单存在且属于本人 | need_info（不区分"不存在"与"不是你的"） |
| `signed_at` 不早于 `shipped_at` | need_info（数据矛盾） |
| `condition_ok is None` | **转人工** |

**验收**：每条一正一反。

**Commit:** `feat(rules): validate 守卫（含 fail-closed）`

---

### Task 7: `query_data`

**Files:**
- Modify: `app/graph/rules_exit.py`
- Test: `tests/test_rules_query.py`

**Step 1: 取单**

`repo_order.get_order(order_id, user_id=...)` —— 归属校验在这一层（S1）。

**Step 2: 失败分支**

- `NotFound` → need_info（**不告诉用户"这单存在但不是你的"**）
- 超时/异常 → 转人工

**验收**：越权用例走 need_info 而非转人工；异常用例走转人工。

**Commit:** `feat(rules): query_data 取单与归属校验`

---

### Task 8: `compliance`（硬闸）

**Files:**
- Modify: `app/graph/rules_exit.py`
- Test: `tests/test_rules_compliance.py`

**四条红线**（spec §compliance）：

1. 退款金额 > 实付
2. 退款金额 ≤ 0 但判定可退
3. 法定不适用商品却判可退
4. 超 7 天无理由却判可退

**第 3、4 条是交叉校验**——`compute` 已判过一次，这里独立再判一次。
不一致说明有 bug，**转人工**。

**验收**：四条各一例；并断言触红线时 `respond` 未被调用。

**Commit:** `feat(rules): compliance 交叉校验与转人工`

---

### Task 9: `respond` + 接入主图

**Files:**
- Modify: `app/core/prompts.py`（`REFUND_RESPOND_*`）
- Modify: `app/graph/rules_exit.py`
- Modify: `app/graph/build.py`（`rules` 节点换成子图）
- Test: `tests/test_rules_respond.py`

**Step 1: prompt**

把 `compute` 的结果原样放进 prompt，要求「**直接引用这个数字，不要自己算**」。

**Step 2: 接入**

```python
g.add_node("rules", build_rules_graph())   # 节点名不变
```

**Step 3: 机械校验（本章先做最简版）**

`assert str(result.refund_amount // 100) in answer` —— 金额（元）必须逐字出现。
完整版留给 Ch06 的 `output_guard`。

**验收**：端到端一条 curl，金额与 `compute` 一致。

**Commit:** `feat(rules): respond 复述金额 + 接入主图`

---

### Task 10: 手算 10 份合同对拍

**Files:**
- Create: `data/eval/refund_10.json`
- Create: `scripts/eval_refund.py`
- Test: `tests/test_refund_contracts.py`

**10 份合同覆盖**：规则表 10 条各一次 + 3 个边界（刚好第 7 天、超期 1 天、2 件退 1 件）。

```json
{
  "id": "C1",
  "order": {...},
  "request": {...},
  "now": "2026-11-05T10:00:00",
  "expected": {"eligible": true, "refund_amount": 269900, "reason_code": "ok"}
}
```

**"手算"的要求**：`expected` 由人工算出后写死，**不能先跑代码再抄结果**。

**验收**：`uv run python scripts/eval_refund.py` → **10/10 一致**。

**Commit:** `test(rules): 手算 10 份合同对拍`

---

### Task 11: 验收

- [ ] `uv run pytest -q` 全绿
- [ ] 10/10 对拍通过
- [ ] 8 状态可达；15 条转移各有测试
- [ ] 端到端：`curl "我这单能退多少"` → 金额正确
- [ ] 触红线场景 → 转人工，`respond` 未调用
- [ ] 更新 `00-tasks.md` P3 勾选
- [ ] 写 `docs/reports/ch03-acceptance.md`

**Commit:** `test: Ch03 验收（退款流水线 + 金额对拍）`

---

## 与 spec 的对应

| spec 验收项 | 落在哪个 task |
|---|---|
| 8 状态可达 | Task 3 / 11 |
| 15 转移 + 7 守卫 | Task 3–8 |
| 手算 10 份对拍 | Task 10 |
| 规则表 10 条正反 | Task 2 / 6 |
| 缺单号列候选 | Task 4 |
| 触红线转人工 | Task 8 |
| 金额逐字一致 | Task 9 |

## 风险

| 风险 | 应对 |
|---|---|
| `compute` 混入时间/IO，测试不稳定 | 函数签名强制传 `now`；测试里断言"无 `datetime.now()`" |
| `interrupt` 恢复时节点从头重跑 | `need_info` 之前只做只读（参考实现的教训） |
| 子图状态与主图不通 | 共用 `ConversationState`；端到端 curl 验证 |
| 规则表与语料漂移 | 规则表每条标来源；语料改了跑评测对比 |
| 金额对拍"先跑代码再抄" | 合同由人工先算，代码后跑；不一致时先怀疑代码 |
