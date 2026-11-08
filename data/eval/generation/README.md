# Ch07 评测集外部生成说明

> 用途：把「用户会怎么说」交给外部模型生成，避免测试集反过来拟合我们的实现。
> **外部模型只生成 query，不生成 gold。**
> 期望路由、金额、字段由规则引擎或人工确认。

---

## 一、交付物

| 文件 | 用途 |
|---|---|
| [fact_card.md](./fact_card.md) | 业务事实卡；每次生成前贴给模型 |
| [prompts.yaml](./prompts.yaml) | 六个批次的 prompt（脚本读取） |
| [raw/](./raw/) | 各模型的原始输出 |
| `scripts/generate_eval_candidates.py` | 批量调用脚本 |

---

## 二、什么交给外部模型，什么不交

| 内容 | 交给外部模型 | 说明 |
|---|---|---|
| B1 知识问法的口语化变体 | ✅ | 同一知识点换说法 |
| B2 订单/物流/库存的提问方式 | ✅ | 只生成问法，字段期望我们定 |
| B3/B4 退款场景描述 | ✅ | 只生成场景，金额/资格我们用规则算 |
| B5 异常输入 | ✅（可选） | 数据异常由我们构造 |
| B6 对抗输入 | ✅ | 错别字、混合意图、越权、注入 |
| B7 投诉/转人工话术 | ✅ | 投诉、赔偿、情绪升级 |
| **gold / 期望答案** | ❌ | 规则引擎 + 人工确认 |
| **路由 / prompt / 代码 / 图结构** | ❌ | 不告诉外部模型 |
| **现有 44 条评测集** | ❌ | 避免它照着仿写 |

**一句话**：给业务事实，不给系统设计；让它当用户，不让它当裁判。

---

## 三、六个批次

| 批次 | 内容 | 默认条数 | 对应桶 |
|---|---|---|---|
| A | 知识问法（退货/运费/发票/会员/券/规格） | 20 | B1 |
| B | 订单 / 物流 / 库存 / 价格问法 | 20 | B2 |
| C | 退款 / 取消场景 | 25 | B3/B4 |
| D | 对抗输入（错单号/混合意图/越权/注入） | 20 | B6 |
| E | 投诉 / 赔偿 / 转人工 | 20 | B7 |
| F | 异常数据输入 | 10 | B5 |

Prompt 正文在 [prompts.yaml](./prompts.yaml)；
`--dry-run` 可以先看拼好的输入，不调用模型。

---

## 四、脚本用法

### 写在 `.env` 里（推荐）

多个模型共用一个 key / base URL 时，写一次即可：

```bash
# .env（已被 gitignore，不要提交）
EVAL_GEN_API_KEY=sk-...
EVAL_GEN_BASE_URL=https://your-gateway.example.com/v1
EVAL_GEN_MODELS=model-a,model-b,model-c
EVAL_GEN_TIMEOUT=120
EVAL_GEN_MAX_RETRIES=2
```

脚本默认读取仓库根 `.env`，**已存在的 shell 环境变量优先**。
也兼容别名：`EVAL_GEN_KEY` / `EVAL_GEN_API_NAME` / `EVAL_GEN_API_BASE`。

**不要把事实卡和 prompt 塞进 `.env`**：多行文本难转义、难 diff、容易误提交。
长文本继续放 `fact_card.md` / `prompts.yaml`。

### 命令

```bash
# 先看 prompt，不花钱
uv run python scripts/generate_eval_candidates.py --all --dry-run

# 跑一个批次（.env 里的所有模型都会跑）
uv run python scripts/generate_eval_candidates.py --batch A

# 跑多个批次
uv run python scripts/generate_eval_candidates.py --batch B,C --count 25

# 跑全部
uv run python scripts/generate_eval_candidates.py --all

# 临时只跑某几个模型（覆盖 EVAL_GEN_MODELS）
uv run python scripts/generate_eval_candidates.py --all --models model-a,model-b

# 临时换 key 的 base URL
uv run python scripts/generate_eval_candidates.py --batch D \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1
```

输出：

```
data/eval/generation/raw/<model>_<batch>.jsonl
```

同名文件已存在时默认跳过；`--force` 覆盖。
脚本会把不同模型的结果互相去重，避免同一句进多份文件。

### 网页模型（没有 API）

Google AI Studio / Gemini 网页、ChatGPT 网页都可以手工生成：

1. 新开对话，只贴 `fact_card.md` + 一个批次的 prompt；
2. 要求只输出 JSONL，不要表格和解释；
3. 把输出保存成文本文件：

```
data/eval/generation/manual/gemini-web_D.txt
```

4. 用导入脚本清洗：

```bash
uv run python scripts/import_generated_candidates.py \
  --input data/eval/generation/manual/gemini-web_D.txt \
  --model gemini-web-2026-11-08 \
  --batch D
```

分几次粘贴用 `--append`；文件已存在且不想追加时用 `--force` 覆盖。

网页方式建议只跑 **D 对抗输入** 和 **E 投诉/转人工**，不要六个批次全手工做。

### 生成评审表

所有 raw 文件收集完后：

```bash
uv run python scripts/build_candidate_review.py
```

输出：

```
data/eval/generation/review/candidates.csv   # Excel / Numbers 可编辑
data/eval/generation/review/candidates.md    # 按建议桶分组，带勾选框
```

CSV 列：

| 列 | 用途 |
|---|---|
| `candidate_id` | 稳定编号，回填 YAML 用 |
| `suggested_bucket` | 按来源批次 + 关键词给的建议，不是结论 |
| `flags` | `injection` / `over_permission` / `complaint` / `near_duplicate` 等 |
| `near_duplicate_of` | 近似重复指向的候选 |
| `decision` | 人工填 `keep` / `rewrite` / `drop` |
| `notes` | 改写说明、gold 备注 |

**建议桶只是提示**，最终由你决定；`test` 集尤其要自己确认。

### 先出短名单（推荐）

385 条全看太重。先跑：

```bash
uv run python scripts/shortlist_candidates.py
```

默认输出 **58 条**：

```
B1=12  B2=6  B3=6  B4=10  B5=4  B6=10  B7=10
```

规则：

- 每桶有上限；
- 先按主题轮转（避免三条「七天从哪天算」）；
- 主题内按模型轮转（保证看到不同模型）；
- 排除 `pii` / `prompt_artifact` / `near_duplicate`。

想调整：

```bash
uv run python scripts/shortlist_candidates.py --per-bucket B1=16,B6=12,B7=12
```

输出：

```
review/shortlist.csv   # 只审这 58 条
review/shortlist.md    # 也可以直接在「决定」列填 keep/rewrite/drop
```

`candidates.csv` 保留全量 385 条，作为备份和后续替换来源。

### 逐条审（CSV 不方便时推荐）

```bash
uv run python scripts/review_candidates.py
```

每屏一条，按键：

| 键 | 含义 |
|---|---|
| `k` / `keep` | 保留 |
| `r` / `rewrite` | 保留但需改写（会提示备注） |
| `d` / `drop` | 丢弃 |
| `s` / `skip` | 先跳过 |
| `q` / `quit` | 退出，已审的自动保存 |

决定写到：

```
review/decisions.jsonl
```

中断后重新运行会跳过已审条目；`--redo` 重新审。`--limit 10` 可以先试 10 条。

---

## 五、输出格式

脚本会校验并清洗模型输出，最终每行：

```json
{"query": "我那个推车不想要了能退吗", "style": "colloquial", "scenario": "退货政策口语问法", "model": "qwen-plus"}
```

| 字段 | 说明 |
|---|---|
| `query` | 用户原话；空值/超过 300 字丢弃 |
| `style` | `formal` / `colloquial` / `multi`；非法值回落 `formal` |
| `scenario` | 模型给的一句话标签，可空 |
| `model` | 由脚本写入，便于追溯 |

**不会**包含：期望答案、金额、资格、路由、桶编号。

---

## 六、回收后的筛选标准

| 检查 | 处理 |
|---|---|
| 与现有 `eval_v1.yaml` 重复或近似 | 丢弃（脚本已做精确去重） |
| 编造了事实卡之外的订单/商品/政策 | 丢弃 |
| 一句话里塞了多个不相关诉求 | 视情况保留到 B6 |
| 带真实 PII / 支付账号 | 丢弃 |
| 只是换标点、没有新表达 | 丢弃 |
| 需要系统判断才能理解 | 保留，标注「歧义」 |
| 适合 B1/B2/B3/B4/B6/B7 | 人工分配桶 |

筛选后每条补：

```yaml
author: "claude|gpt|qwen|..."
reviewed_by: "lidong"
split: "dev|test"
```

`test` 集由你最终确认并冻结；外部模型生成不等于盲测。

---

## 七、给多个模型的建议

1. **至少两个不同厂商的模型**，避免同一个模型家族的偏好；
2. 每个模型拿到**完全相同的事实卡和 prompt**，不要互相看结果；
3. 每个批次要 20–25 条，最后合并去重，不要按模型分配桶；
4. 不要告诉模型「我们在做评测集」「这是给面试用的」，只说「模拟用户」；
5. 生成结果先落盘，不要边生成边改 prompt。

---

## 八、明确不要发给外部模型的内容

- `docs/superpowers/` 下的 spec / plan；
- 系统 prompt、`PROMPT_GUARD`、路由表、graph 结构；
- `eval_v1.yaml` / `evalflow_v1.json` / 现有 gold；
- 真实 API key、数据库连接串、用户 PII；
- 面试材料、简历措辞。

业务事实卡是唯一允许提供的上下文。
