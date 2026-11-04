# Ch05 · 多轮与上下文 · 改动记录

> 对应 spec：[ch05-multi-turn-design.md](../superpowers/specs/ch05-multi-turn-design.md)
> 对应 plan：[ch05-multi-turn.md](../superpowers/plans/ch05-multi-turn.md)
> 格式说明见 [00-change-log-overview.md](./00-change-log-overview.md)

| # | 日期 | 任务 | commit |
|---|---|---|---|
| 1 | 2026-11-04 | Task 1 · 会话生命周期 + 消息落库 | 未提交 |

---

## 1 · Task 1 · 会话生命周期 + 消息落库（未提交）

### 新增

| 文件 | 内容 |
|---|---|
| `app/core/history.py` | `save_user_message` / `save_assistant_message` / `load_messages`；`db-{id}` 锚点；meta 解析 |
| `tests/core/test_history.py` | 5 条：锚点、幂等、点选空消息、error 状态、坏 meta |

### 修改与原因

| 文件 · 符号 | 旧逻辑 | 新逻辑 | 为什么旧的撑不住 |
|---|---|---|---|
| `api/chat.py` · `run_turn` | 每轮只用传入的 `conversation_id`，不建会话、不落消息 | 先建/取会话（归属校验）→ 落用户消息 → 跑图 → 落助手消息 | 下一轮要能回放，必须先把这轮写下来 |
| `api/chat.py` · `ack` | `{"event": "ack"}` | 带 `conversation_id` / `user_message_id` | 前端需要拿到会话 id 才能发下一轮 |
| `api/chat.py` · `done` | `{"event": "done"}` | 带 `message_id` | 前端/审计需要知道助手消息落在哪一行 |
| `api/chat.py` · 异常路径 | 直接上抛，用户消息悬空 | 补一条 `status="error"` 的助手消息再上抛 | 回放时不能只有问、没有答 |
| `main.py` · `ChatRequest` | `message: str` 必填 | 增加可选 `selected_action`；`message` 默认空串 | Ch04 的选单卡片需要结构化回传，不能只靠拼文本 |
| `main.py` · `chat` | 只传 message / conversation_id | 透传 `client_message_id` / `selected_action` | K4 幂等与 Ch05 槽位填充都需要 |
| `scripts/try.py` | 只有单轮 `graph` 模式 | 增加 `chat` 交互模式，复用 `conversation_id` | 手工验证跨轮比一直跑单轮图直观 |

### 四个设计点

**1. 用户消息与助手消息分开事务。**
用户消息在跑图前提交：即使后面模型超时，审计里也能看到用户问了什么。
助手消息在跑图后写；失败时补一条 `status="error"`，避免「有问无答」。

**2. 锚点用 DB 主键，不用序号。**
`id=f"db-{row.id}"`。后面滑窗/摘要边界都用它，消息补录或删除不会错位。

**3. `session` 可注入。**
沿用仓库层约定：生产不传，测试传临时库；否则这层只能打桩，测不到真实 SQL。

**4. `selected_action` 存在用户消息的 `meta` 里。**
结构化点选可以没有文字内容；把原始动作存进 meta，回放时能还原用户点了什么。

### 验证

- `uv run pytest -q` → **716 passed, 39 skipped**
- `uv run ruff check app tests scripts` → All checks passed
- `tests/core/test_history.py` + `tests/api/test_chat_sse.py` → 25 passed
