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
| 文件 · 位置 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |

### 删除 / 重命名（有才写）
| 文件 | 处理 |

### 验证
- 测试：N passed
- 手工：实际执行结果
```

**修改必须写清三件事**：旧逻辑是什么、新逻辑是什么、**旧逻辑为什么撑不住新需求**。只写"改了什么"，三个月后看不出原因。

## 章节索引

| 章节 | 内容 | 记录文件 | 状态 |
|---|---|---|---|
| Ch01 | 数据地基（10 表 / seed / 越权） | [ch01-data-foundation-changes.md](./ch01-data-foundation-changes.md) | ✅ 8 节 |
| Ch02 | 主流程骨架 | [ch02-walking-skeleton-changes.md](./ch02-walking-skeleton-changes.md) | 🚧 1 / 10 节 |
| Ch03 | C 出口状态机 | `ch03-rule-exit-changes.md` | ⏳ |
| Ch04 | B 出口 | `ch04-data-exit-changes.md` | ⏳ |
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
