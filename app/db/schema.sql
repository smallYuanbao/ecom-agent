-- 建表脚本（依据：M1 / M4 / K1 / K4 / K5 / 飞轮）
-- SQLite 方言；生产可迁移到 PostgreSQL。

CREATE TABLE IF NOT EXISTS conversations (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id           TEXT NOT NULL,
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at        DATETIME,                       -- K5 软删
  summary           TEXT,                           -- M2 摘要
  summary_upto_msg_id INTEGER,
  active_flow       TEXT,                           -- M4: refund / price_protection / NULL
  pending_question  TEXT,                           -- M4: order_id / confirm / NULL
  draft             TEXT                            -- M4: JSON 已收集要素
);

CREATE TABLE IF NOT EXISTS messages (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id   INTEGER NOT NULL REFERENCES conversations(id),
  role              TEXT NOT NULL,                  -- user / assistant / tool
  content           TEXT,
  tool_calls        TEXT,                           -- JSON
  tool_call_id      TEXT,
  status            TEXT,                           -- C1: completed / cancelled / error
  client_message_id TEXT,                           -- K4 幂等键
  meta              TEXT,                           -- JSON: intent / route / citations
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uk_msg_idem
  ON messages(conversation_id, client_message_id)
  WHERE client_message_id IS NOT NULL;              -- K4

CREATE TABLE IF NOT EXISTS faq (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  question          TEXT NOT NULL,
  answer            TEXT NOT NULL,
  source_type       TEXT NOT NULL,                  -- K5: flywheel / manual
  source_question_id INTEGER,                       -- K5: 来自哪条低置信问题
  reviewed_at       DATETIME,
  kb_version        TEXT,
  sync_status       TEXT NOT NULL DEFAULT 'pending',-- K1: pending / synced
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tickets (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id   INTEGER NOT NULL,
  order_id          TEXT,
  ticket_type       TEXT,
  description       TEXT,
  request_id        TEXT NOT NULL,                  -- D11 幂等
  status            TEXT NOT NULL DEFAULT 'open',
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_ticket_idem ON tickets(request_id);

CREATE TABLE IF NOT EXISTS trace_span (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id          TEXT NOT NULL,                  -- L7
  span_id           TEXT NOT NULL,
  parent_span_id    TEXT,
  name              TEXT NOT NULL,                  -- node:compute / tool:query_order
  start_at          DATETIME NOT NULL,
  end_at            DATETIME,
  attrs             TEXT                            -- JSON
);
CREATE INDEX IF NOT EXISTS idx_span_trace ON trace_span(trace_id);

CREATE TABLE IF NOT EXISTS low_conf_questions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id   INTEGER,
  question          TEXT NOT NULL,
  normalized        TEXT,
  source            TEXT,                           -- retrieval_low_conf / feedback / rule_conflict
  reason            TEXT,
  retrieved         TEXT,                           -- JSON: [...] / [] / NULL 三态
  status            TEXT NOT NULL DEFAULT 'pending',
  merged_into       INTEGER,
  occurrence        INTEGER NOT NULL DEFAULT 1,
  kb_ref            TEXT,
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at       DATETIME
);
