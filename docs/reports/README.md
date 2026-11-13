# reports 目录索引

> 这里放**运行记录与结论**，不放设计。设计在 `docs/design/`，spec / plan 在 `docs/superpowers/`。
>
> **文件保持平铺**：改名/移动会打断 80 多处引用（含代码 docstring 与脚本），
> 找文件靠这张“按目的找”的表，不靠目录层级。

**当前进度**：Ch01–Ch07 已完成（Ch07 见 [ch07-acceptance.md](./ch07-acceptance.md)）；下一步 Ch08 前端。

> **第一次读 Ch07 / 想重新理解全章**：先看
> [ch07-overview.md](./ch07-overview.md)——它把“为什么做、每个任务做什么、
> 任务之间什么关系、哪些结论能信”串成了一条线。
>
> **想复习整个项目 / 准备面试**：看
> [project-review.md](./project-review.md)——主线图、章节四格卡、决策地图、
> 三个故事和自测题。当前已完成 Ch02 / Ch03，其余章节待补。

---

## 一、最快入口：我想……

| 我想…… | 先看 | 再看 |
|---|---|---|
| 复习整个项目 / 准备面试 | [project-review.md](./project-review.md) | [ch07-overview.md](./ch07-overview.md) |
| 整体理解 Ch07 | [ch07-overview.md](./ch07-overview.md) | [ch07-acceptance.md](./ch07-acceptance.md) |
| 看当前质量基线 | [run-03.md](./run-03.md) | [judge-variance.md](./judge-variance.md)（判断变化是不是噪声） |
| 判断一次指标变化可不可信 | [judge-variance.md](./judge-variance.md) | [run-02.md](./run-02.md) |
| 调证据阈值 / 语义闸 | [gate-thresholds.md](./gate-thresholds.md) | [gate-eval-semantic.md](./gate-eval-semantic.md) |
| 改语料 / FAQ / 切分 | [retrieval-eval-faq.md](./retrieval-eval-faq.md) → [knowledge-eval-semantic.md](./knowledge-eval-semantic.md) | [quality-propagation.md](./quality-propagation.md) |
| 决定 dense / BM25 / hybrid | [retrieval-eval-hard.md](./retrieval-eval-hard.md) | [retrieval-eval-faq.md](./retrieval-eval-faq.md) |
| 改退款判定或 prompt | [refund-hard.md](./refund-hard.md) | [refund-eval.md](./refund-eval.md) |
| 准备面试 | [../interview/README.md](../interview/README.md) | [resume-positioning.md](./resume-positioning.md) |
| 本地环境起不来 | [local-env.md](./local-env.md) | [manual-checks.md](./manual-checks.md) |
| 指标数字看不懂 | [metrics-glossary.md](./metrics-glossary.md) | 对应报告的“结论”一节 |

## 二、当前结论（一眼看完）

| 主题 | 当前结论 | 依据 |
|---|---|---|
| 检索策略 | **dense**；hybrid 无显著收益，BM25 明显更差 | [retrieval-eval-hard.md](./retrieval-eval-hard.md) |
| 证据闸 | **0.55 + 语义带 0.05**；双零区间 0.53–0.56 | [gate-thresholds.md](./gate-thresholds.md) |
| 质量传导 | 受控错误证据 10 条，**0 编造** | [quality-propagation.md](./quality-propagation.md) |
| run-03 | recall 1.00 / correctness 0.9977；确定性 44/44 | [run-03.md](./run-03.md)、[run-local-06.md](./run-local-06.md) |
| 退款判定 | held-out 15/15（CI 80–100%；规则顺序组为参照组）；dev 10/10；金额 10/10 | [refund-hard.md](./refund-hard.md) |

## 三、全部报告（按类型）

### 3.1 改动记录（每章一份，只增不改）

| 文件 | 覆盖内容 | 状态 |
|---|---|---|
| [00-change-log-overview.md](./00-change-log-overview.md) | 格式说明 + 章节索引。**新章先在这里登记** | ✅ |
| [ch01-data-foundation-changes.md](./ch01-data-foundation-changes.md) | 数据地基：10 表、seed、越权 | ✅ |
| [ch02-walking-skeleton-changes.md](./ch02-walking-skeleton-changes.md) | 主流程骨架：意图、路由、检索、知识出口 | ✅ |
| [ch03-rules-exit-changes.md](./ch03-rules-exit-changes.md) | C 出口：退款 9 状态流水线、判定、评测 | ✅ |
| [ch04-data-exit-changes.md](./ch04-data-exit-changes.md) | B 出口：订单 / 物流 / 库存查询 | ✅ |
| [ch05-multi-turn-changes.md](./ch05-multi-turn-changes.md) | 多轮：消息落库、锚点、窗口、指代、park、ReAct | ✅ |
| [ch06-security-changes.md](./ch06-security-changes.md) | 安全：越权、404、Prompt 四区、输出检查、日志 | ✅ |
| [ch07-eval-changes.md](./ch07-eval-changes.md) | 评测：44 条评测集、评测门面、EvalFlow、阈值与传导 | 🚧 |

### 3.2 验收报告（一章做完时写）

| 文件 | 覆盖内容 | 状态 |
|---|---|---|
| [ch02-acceptance.md](./ch02-acceptance.md) | 主流程骨架验收 | ✅ |
| [ch03-acceptance.md](./ch03-acceptance.md) | 退款流水线：清单、指标、转人工、延迟、局限 | ✅ |
| [ch04-acceptance.md](./ch04-acceptance.md) | 数据查询：三类查询、边界、延迟、零模型调用 | ✅ |
| [ch05-acceptance.md](./ch05-acceptance.md) | 多轮与 ReAct：六条场景、park、指代、延迟 | ✅ |
| [ch06-acceptance.md](./ch06-acceptance.md) | 安全：越权、404、注入、输出检查、日志脱敏 | ✅ |
| [ch07-acceptance.md](./ch07-acceptance.md) | 评测验收：清单、指标、边界、复现命令 | ✅ |

### 3.3 端到端评测

| 文件 | 覆盖内容 | 状态 |
|---|---|---|
| [run-01.md](./run-01.md) | EvalFlow 首轮基线（旧 KB，44 条） | 📦 历史 |
| [run-02.md](./run-02.md) | 新 KB + 语义闸；与 run-01 逐桶对比 | 📦 历史 |
| [run-03.md](./run-03.md) | 当前 prompt 的质量快照；快照字段已修正 | ✅ 当前 |
| [run-local-01.md](./run-local-01.md) | 本地确定性跑分（第 1 轮） | 📦 历史 |
| [run-local-02.md](./run-local-02.md) | 本地确定性跑分（第 2 轮） | 📦 历史 |
| [run-local-03.md](./run-local-03.md) | 本地确定性跑分（第 3 轮） | 📦 历史 |
| [run-local-04.md](./run-local-04.md) | 本地确定性跑分（第 4 轮，test 集净化后） | 📦 历史 |
| [run-local-05.md](./run-local-05.md) | 本地确定性跑分（新 KB，44/44） | 📦 历史 |
| [run-local-06.md](./run-local-06.md) | 当前 prompt 下的确定性回归，**44/44** | ✅ 当前 |

> `run-NN` 是 EvalFlow 端到端快照（含 judge 分）；`run-local-NN` 是确定性脚本，
> 只查路由 / 金额 / 动作 / 引用，**不受 judge 方差影响**。

### 3.4 检索与知识

| 文件 | 覆盖内容 | 状态 |
|---|---|---|
| [retrieval-eval.md](./retrieval-eval.md) | 三策略对比（旧 41-chunk 语料） | 📦 历史 |
| [retrieval-eval-faq.md](./retrieval-eval-faq.md) | 加 FAQ 后的三策略对比 | 📦 历史 |
| [retrieval-eval-hard.md](./retrieval-eval-hard.md) | 稀有词困难集：型号 / 法条 / 地区 / 发票 | ✅ 当前 |
| [knowledge-eval.md](./knowledge-eval.md) | 知识出口覆盖率与引用质量 | 📦 历史 |
| [knowledge-eval-faq.md](./knowledge-eval-faq.md) | 加 FAQ 后的知识覆盖 | 📦 历史 |
| [knowledge-eval-semantic.md](./knowledge-eval-semantic.md) | 语义闸后的覆盖与放行（42/42） | ✅ 当前 |
| [quality-propagation.md](./quality-propagation.md) | 检索→答案 2×2、四格来源、人工核验 | ✅ 当前 |

### 3.5 证据闸

| 文件 | 覆盖内容 | 状态 |
|---|---|---|
| [gate-eval.md](./gate-eval.md) | 单阈值的判定矩阵与 Pareto 前沿 | 📦 历史 |
| [gate-eval-faq.md](./gate-eval-faq.md) | 加 FAQ 后的闸校准 | 📦 历史 |
| [gate-eval-semantic.md](./gate-eval-semantic.md) | 语义闸：边界样本、误杀与延迟 | ✅ 当前 |
| [gate-thresholds.md](./gate-thresholds.md) + [.csv](./gate-thresholds.csv) | 0.40–0.75 曲线：机械 / 语义的误拦与误放 | ✅ 当前 |

### 3.6 退款判定

| 文件 | 覆盖内容 | 状态 |
|---|---|---|
| [refund-eval.md](./refund-eval.md) | 模型 / 规则 / 流水线三列，未命中归因 | 📦 历史（已加复跑更正） |
| [refund-hard.md](./refund-hard.md) | dev + held-out 15 条、Wilson CI、per-class、失败归因 | ✅ 当前 |

### 3.7 测量方法

| 文件 | 覆盖内容 | 状态 |
|---|---|---|
| [judge-variance.md](./judge-variance.md) | 同批 44 条跑 3 次；哪些 Δ 可信 | ✅ 当前 |
| [metrics-glossary.md](./metrics-glossary.md) | hit@3 / MRR / 误拦率 / 误放率等指标释义 | ✅ |

### 3.8 环境与手工检查

| 文件 | 覆盖内容 |
|---|---|
| [local-env.md](./local-env.md) | Chroma（Docker）、Ollama（bge-m3）、端口、集合名 |
| [manual-checks.md](./manual-checks.md) | 不依赖自动化的手工验证步骤 |

### 3.9 简历与面试

| 文件 | 覆盖内容 |
|---|---|
| [resume-positioning.md](./resume-positioning.md) | 简历措辞与实现状态对照；每条标注“能不能写、会被追问什么” |
| [../interview/README.md](../interview/README.md) | 面试材料索引 + 跨章主线 |
| [../interview/ch01-data-foundation.md](../interview/ch01-data-foundation.md) | Ch01：双写 DDL、幂等、越权、seed 覆盖度 |
| [../interview/ch02-retrieval-skeleton.md](../interview/ch02-retrieval-skeleton.md) | Ch02：查表路由、检索选型、证据闸、RAG 评测 |
| [../interview/ch03-refund.md](../interview/ch03-refund.md) | Ch03：模型判定 + 代码复算、评测设计、踩坑故事 |
| [../interview/ch04-data-exit.md](../interview/ch04-data-exit.md) | Ch04：零模型调用、固定链、越权防枚举、模板回答 |
| [../interview/ch05-multi-turn.md](../interview/ch05-multi-turn.md) | Ch05：DB 三字段恢复、窗口、park、锁、只读 ReAct |
| [../interview/database-qa.md](../interview/database-qa.md) | 数据库问答：索引 / 事务 / WAL / async ORM |
| [../interview/self-test.md](../interview/self-test.md) | 自测方法：抽题表、评分标准、模拟面试与演示脚本 |

## 四、文件名怎么读

| 模式 | 含义 | 例子 |
|---|---|---|
| `chNN-*-changes.md` | 某一章的改动记录，**只增不改** | `ch07-eval-changes.md` |
| `chNN-acceptance.md` | 某一章的验收结论 | `ch06-acceptance.md` |
| `run-NN.md` | EvalFlow 端到端快照（含 judge 分） | `run-02.md` |
| `run-local-NN.md` | 确定性脚本跑分（不受 judge 方差影响） | `run-local-05.md` |
| `*-eval.md` | 某一主题的评测原始数据 / 结论 | `retrieval-eval.md` |
| `*-faq.md` | 加 FAQ 后的同一实验（**不覆盖旧报告**） | `gate-eval-faq.md` |
| `*-semantic.md` | 加语义闸后的同一实验 | `knowledge-eval-semantic.md` |
| `gate-thresholds.*` | 阈值曲线（md 给人看，csv 给脚本/画图） | `gate-thresholds.csv` |

**同一实验的新轮次不覆盖旧报告**：新建带后缀的文件，旧文件标 📦 历史，
这样“改了什么导致数字变化”可追溯。

## 五、新增报告时的检查清单

- [ ] 写清**数据来源**（哪份语料 / `kb_version`、哪组样本、跑了几次）
- [ ] 写清**样本量**和结论的**适用边界**（能不能推广）
- [ ] 给出**失败样本的归因**，不只报总分
- [ ] 自动生成的报告在开头写生成时间和语料版本
- [ ] 在本文件 3.x 表里登记一行，并标状态（✅ / 📦 / 🚧 / ⏳）
- [ ] 决策写进 `docs/superpowers/specs/`，报告只放数据与结论，不替代 spec
