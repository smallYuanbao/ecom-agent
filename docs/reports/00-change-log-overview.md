# 改动记录 · 索引

> **用途**：每个任务完成后，在该章对应的改动文件末尾追加一节。
> **与 git 的关系**：git 回答"改了什么"（diff），这里回答"**为什么改、旧逻辑为什么不成立**"——互补，不重复。
> **组织方式**：与 `specs/`、`plans/` 一致，**按章拆文件**，一章只记本章的操作。

## 文件命名

```
docs/reports/
  00-change-log-overview.md              # 本文件：格式说明 + 索引
  chNN-<slug>-changes.md                 # 每章一份，slug 与 spec/plan 一致
```

对照关系：

| 层 | 目录 | 命名 |
|---|---|---|
| 为什么这么设计 | `docs/design/` | `电商客服-*.md` |
| 这一章做成什么 | `docs/superpowers/specs/` | `chNN-<slug>-design.md` |
| 怎么一步步做 | `docs/superpowers/plans/` | `chNN-<slug>.md` |
| **实际改了什么、为什么** | **`docs/reports/`** | **`chNN-<slug>-changes.md`** |

## 每节的格式

```markdown
## N · 任务名（`commit-hash`）

### 新增
| 文件 | 内容 |

### 修改与原因
| 文件 › 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |

### 关联（有才写）
- 替换：Ch0X §N · `hash`（哪个函数的旧实现）

### 删除 / 重命名（有才写）
| 文件 | 处理 |

### 验证
- 测试：N passed
- 手工：实际执行结果
```

**修改必须写清三件事**：旧逻辑是什么、新逻辑是什么、**旧逻辑为什么撑不住新需求**。只写"改了什么"，三个月后看不出原因。

## 位置怎么写

| 写法 | 例子 | 评价 |
|---|---|---|
| **符号名** | `app/graph/nodes.py › classify_intent` | ✅ 相对稳定，可 grep、可 `git log -L` |
| 行号 | `nodes.py L42-55` | ❌ 每次增删行都会漂移，三个月后必然对不上 |

## 时效声明（重要）

**记录写的是当时的决策与位置，不保证"现在代码还长这样"。**

代码会被后续任务替换（如 `classify_intent` 的占位实现会被 Task 2 换掉）。处理原则：

| 情况 | 怎么处理 |
|---|---|
| 函数还在、内容改过 | 旧记录**不改**；新记录在「关联」里写「替换：Ch0X §N · hash」 |
| 函数改名 | 同上，并在新记录里写明旧名 |
| 函数被删除 | 同上 |

**追加，不改写。** 改动记录是只增日志，不是当前状态的快照——当前状态以代码和 `git log` 为准。

这样做的代价是：读旧记录时可能找不到对应代码。用下面的命令定位。

## 找不到对应代码时

```bash
# 某个函数的完整演变（含每次改动）
git log -L :classify_intent:app/graph/nodes.py --oneline

# 哪次提交删掉了某段代码
git log -S "PLACEHOLDER_TEXT" --oneline -- app/graph/nodes.py

# 还原记录当时那一版文件
git show 98abfb6:app/graph/nodes.py

# 谁替换了某一节（依赖新记录写了「关联」）
grep -rn "替换：Ch02 §1" docs/reports/
```

**最后一条是这套机制的关键**：单向链接就够——新记录指向旧记录，旧记录永不回改。

## 章节索引

| 章节 | 内容 | 记录文件 | 状态 |
|---|---|---|---|
| Ch01 | 数据地基（10 表 / seed / 越权） | [ch01-data-foundation-changes.md](./ch01-data-foundation-changes.md) | ✅ 8 节 |
| Ch02 | 主流程骨架 | [ch02-walking-skeleton-changes.md](./ch02-walking-skeleton-changes.md) | ✅ 10 / 10 节 |
| Ch03 | C 出口（退款规则） | [ch03-rules-exit-changes.md](./ch03-rules-exit-changes.md) | ✅ 18 / 18 节（验收见 [ch03-acceptance.md](./ch03-acceptance.md)） |
| Ch04 | B 出口（数据查询） | [ch04-data-exit-changes.md](./ch04-data-exit-changes.md) | 🔄 1 节（Task 2–9 待做） |
| Ch05 | 多轮与上下文 | `ch05-multi-turn-changes.md` | ⏳ |
| Ch06 | 安全 | `ch06-security-changes.md` | ⏳ |
| Ch07 | 评测 | `ch07-eval-changes.md` | ⏳ |
| Ch08 | 前端 | `ch08-frontend-changes.md` | ⏳ |

## 与其他报告的关系

| 文件 | 记什么 |
|---|---|
| `00-change-log-overview.md` + `chNN-*-changes.md` | **每次改动**的意图与原因 |
| [local-env.md](./local-env.md) | 外部依赖现状（Chroma / Ollama / 端口） |
| [resume-positioning.md](./resume-positioning.md) | 简历措辞与实现状态的对照 |
