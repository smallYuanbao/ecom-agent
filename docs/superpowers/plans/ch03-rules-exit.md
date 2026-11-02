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
@dataclass(frozen=True)
class Judgement:
    eligible: bool
    reason_code: str


def compute_amount(
    order: OrderSnapshot, judgement: Judgement, request: RefundRequest
) -> int
```

```
不可退                           → 0
待发货（整单取消）                → 全额
未签收（reject_then_refund）     → 全额
其余可退                         → floor(实付 × 退货件数 / 总件数)
```

**测试**：整单退、部分退（2 退 1）、件数=总件数、不可退、件数越界（>总件数 → 按总件数封顶）、
待发货全额、拒收退款全额、件数为 0 / 负、总件数为 0（不除零）、floor 不四舍五入、纯函数三条。

**验收**：纯函数断言（无 `await`、无 `now()`）。

**Commit:** `feat(rules): 退款金额纯函数`

---

### Task 4: 子图骨架（9 状态 / 17 转移，全空实现）

**Files:**
- Create: `app/graph/rules_exit.py`
- Test: `tests/test_rules_graph.py`

**Step 1: 9 个节点全用 `_stub`**，边按 spec 转移表写；守卫各是一个函数。

> 例外：`need_info` 用单独的空实现写 `pending_question`。
> 若它也返回 `{}`，`after_need_info` 会把它接回 `validate`，而空实现的 `validate`
> 又放行到 `query_data`，形成 `need_info → validate → query_data → need_info` 死循环。
> 写一个字段让它"停下来等用户"，骨架才能终止。

**Step 1.5: 状态字段**

`ConversationState` 增加退款流水线的中间字段（`order_id` / `refund_draft` /
`validate_errors` / `order_snapshot` / `refund_judgement` / `refund_amount` /
`compliance_violation` / `pending_question` / `transfer_reason` 等）。
子图与主图共用同一份 schema，Task 11 才能直接 `add_node("rules", build_rules_graph())`。

**Step 2: 结构测试**

断言 9 个节点都在图里；每个守卫的返回值集合与 `add_conditional_edges` 的键一致；
边集合与 spec 的 17 条转移逐条相等。

**Commit:** `feat(rules): 9 状态子图骨架（空实现）`

---

### Task 5: `intake` 与 `need_info`

**Files:** Modify `app/graph/rules_exit.py`；Test `tests/test_rules_intake.py`

- `intake`：正则 `(?<!\d)(\d{4,})(?!\d)` 抽订单号；抽到清空 `pending_question`
- `need_info`：缺单号 → `list_user_orders` 取最近 3 单，
  写入 `order_candidates` + `actions=[{"type": "select_order"}]`
- **不调用 `interrupt()`**：它需要 checkpointer 才能 resume（Ch05 才做），
  且会丢弃节点返回值，`answer` / 候选列表到不了 state。跨轮恢复走 DB
- `ConversationState` 补 `user_id`；API 填演示身份 `settings.demo_user_id`

**验收**：单号直达、无线索列候选、resume 回填三条测试。

**Commit:** `feat(rules): intake 抽单号 + need_info 列候选`

---

### Task 6: `extract`（模型抽要素）

**Files:** Modify `app/core/prompts.py`、`app/graph/rules_exit.py`；Test `tests/test_rules_extract.py`

```python
EXTRACT_PARAMETERS = {
    "reason_code": ["quality", "no_reason", "other"],  # 用户没提就不填
    "item_count": int,                                 # 没说数量不填，不默认 1
    "condition_ok": bool,                              # 三态：拿不准就省略（= null）
    "packaging_opened": bool,                          # 只拆快递箱 → false
}
# required 刻意留空：四个要素都可能"用户没说"，强制必填会逼模型编值
```

**验收**：4 条样例用真模型抽取正确；"拿不准"时确实省 `condition_ok`。

> 有 `CHAT_API_KEY` 才跑真模型样例，CI 里 skip。洗数据逻辑（枚举越界、
> 字符串数字、假布尔）始终被测。
>
> **真模型断言允许保守省略、不允许猜错**：模型偶尔会少给一个字段，
> 那是安全方向；把「只拆快递箱」判成不完好才是危险方向。精确等值断言会随机失败。

**Commit:** `feat(rules): extract 结构化抽取`

---

### Task 7: `validate`

**Files:** Modify `app/graph/rules_exit.py`；Test `tests/test_rules_validate.py`

三个守卫：要素齐全、订单属于本人、日期不矛盾。`condition_ok` 缺失**不拦**（交给 judge）。

- 错误码封闭集合：`missing_order_id` / `missing_reason_code` / `order_not_found` / `date_anomaly`
- 缺失、查无 → `need_info`；追问 2 次 → 转人工
- **日期倒挂立即转人工**：用户改不了 ERP/时区造成的矛盾
- 读订单时顺手写 `order_snapshot`，Task 8 直接复用，避免一轮两次查库

**验收**：每条守卫一正一反；越权与不存在返回同一个错误码（不泄露订单存在）。

**Commit:** `feat(rules): validate 守卫`

---

### Task 8: `query_data`（订单 + 政策）

**Files:** Modify `app/graph/rules_exit.py`；Test `tests/test_rules_query.py`

```python
# 先复用 validate 写的快照；只有缺失时才补查
order = await repo_order.get_order(order_id, user_id=...)
evidence = await retrieval.search(f"退款 {中文原因} {order.status}", top_k=5)
```

**前置**：给 `orders` 加 `category`（下单时快照），同步 `models.py` / `schema.sql` / seed。
不带这个字段，第六条与第七条没法判。

**失败分支**：订单 `NotFound` → need_info；**政策为空或检索异常** → 转人工。

**验收**：越权走 need_info；检索为空走转人工；有快照时不再查库。

**Commit:** `feat(rules): query_data 取单 + 政策检索`

---

### Task 9: `judge`（模型判资格）

**Files:** Modify `app/core/prompts.py`、`app/graph/rules_exit.py`；Test `tests/test_rules_judge.py`

**Step 1: prompt**

给四样东西：**规则表（人话版）** + **政策原文（带编号）** + **订单快照** + **申请要素**。
要求输出 `{eligible, reason_code, confidence, cited_article?}`，`reason_code` 是封闭集合。

**Step 2: 校验 + 门槛**

- 输出整条校验：布尔 / 枚举 / 区间 / **结论与理由码自洽**
- `confidence < 0.7` → 转人工
- **`condition_ok` 为 `null` 时禁止判 `not_intact`**（真模型实测后补进 prompt）

**Step 3: 测试（mock 模型）**

- 高分 → 进 compute
- 低分 → 转人工
- 格式错误 / 自相矛盾 → 转人工
- prompt 里四块材料齐全

> 真模型准确率不在本节测——那是 Task 12 的 gold set。

**Commit:** `feat(rules): judge 模型判定资格`

---

### Task 10: `compliance`（红线 + 独立复算）

**Files:** Modify `app/graph/rules_exit.py`；Test `tests/test_rules_compliance.py`

**前置**：实现 `compute` 节点（计划漏了）——`compute_amount` 只是纯函数，
流水线里得有人调它，否则 `refund_amount` 永远是空的，红线 2 会误伤所有可退单。

**两件事**：

1. 四条红线（金额异常、法定不适用却可退、超期无理由却可退…）
2. **调用 `judge_eligibility` 复算**，比 `(eligible, reason_code)` 一对；
   不一致 → 转人工 + 写 `compliance:violation` span；代码返回 `None` 不算分歧

**验收**：四条红线各一例；分歧用例确实转人工；一致用例放行；trace 确实落库。

**Commit:** `feat(rules): compliance 红线与复算**

---

### Task 11: `respond` + 接入主图

**Files:** Modify `app/core/prompts.py`、`app/graph/rules_exit.py`、`app/graph/build.py`

- prompt 里给**算好的字符串**（「1249.00 元」），不是数字——给数字模型会自己换算
- `g.add_node("rules", build_rules_graph())`（节点名不变）；删掉 `nodes.rules_exit` 占位
- 最简机械校验：金额必须出现在回答里，**忽略空格与千分位**；没有就追加一句
- 子图每个节点写 `rules:<node>` span（父图只看得到 `node:rules` 一行）

**验收**：端到端跑 `run_turn`，金额与 `compute_amount` 一致、只出现一次；
`trace_span` 里 9 个 `rules:*` 都在。

**Commit:** `feat(rules): respond 复述金额 + 接入主图`

---

### Task 12: gold set 与手算对拍

**Files:** Create `data/eval/refund_gold_20.json`、`data/eval/refund_gold_hard_10.json`、`data/eval/refund_10.json`、`scripts/eval_refund.py`

**三批数据**：

| 文件 | 用途 | 门槛 |
|---|---|---|
| `refund_gold_20.json` | 规则覆盖范围内的 20 单，测 `judge` **有没有退化** | 准确率 ≥ 90% |
| `refund_gold_hard_10.json` | 规则判不了（A 组 4 条）或判得错（B 组 6 条），测模型**增量价值** | A 组 ≥ 3/4；B 组 ≥ 4/6 |
| `refund_10.json` | 10 份合同，测 `compute_amount` 金额 | 10/10 一致 |

**为什么必须分两组报**：规则实现在 `refund_gold_20.json` 上已经 20/20。
只报总准确率的话，模型最好也只是"打平规则"，看不出它存在的理由。
困难组才是模型的考场——**如果模型在困难组上也拿不到 7/10，就该直接用规则**。

**规则基线（已实测）**：困难组 2/10（A 组 2/4、B 组 0/6）。

> ⚠️ **B 组的 0/6 是构造出来的，不是抽样结果。**
> 这 6 条本来就是"怀疑规则判错"才挑的，所以不能拿它估计规则在线上真实准确率，
> 也不能拿"超过 0"当门槛。B 组用**绝对门槛 4/6**：说明模型不是靠运气赢一个已知错误的基线。

> **分组会随规则演进而变。** H04（质量缺陷导致的使用痕迹）原在 B 组，
> 修完规则 8 后代码改为返回 `None`，因此移入 A 组。分组必须描述**当前**的规则行为。

**"手算"的要求**：`expected` 由人工先算好写死，**不能先跑代码再抄**。

**验收**：`uv run python scripts/eval_refund.py` 输出三张表（简单组 / 困难组 / 金额）。
每张表同时给三列：**模型**（验收对象）、**规则**（基线）、**流水线**（诊断）。

> 流水线 = 模型 + `compliance` 后的最终结论。它同时受规则质量影响，
> 所以**不拿来验收**——否则分不清"规则错了"还是"模型错了"。
> 实跑数据：模型 7/10、规则 2/10、流水线 5/10，差的 2 条是规则判错导致正确答案被升级为转人工。

**Commit:** `test(rules): 判定 gold set 与金额对拍`

---

### Task 13: 验收

- [ ] `uv run pytest -q` 全绿
- [ ] 简单组准确率 ≥ 90%（不退化）；分歧率 < 10%
- [ ] 困难组 A 组 ≥ 3/4（不劣于规则基线），B 组 ≥ 4/6（绝对门槛）
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
| 简单组准确率 ≥ 90% | Task 12 |
| 困难组 ≥ 7/10 | Task 12 |
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
