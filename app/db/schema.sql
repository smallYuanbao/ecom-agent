-- 电商客服 Agent · 建表脚本
-- 字段完整释义见 docs/superpowers/specs/ch01-data-foundation-design.md「数据模型」
-- 通用约定：时间存 UTC；JSON 以 TEXT 存；金额单位为「分」。

-- ============ 系统表 ============

-- 会话：一次对话的容器，同时是图的 thread_id 与跨轮状态锚点
CREATE TABLE IF NOT EXISTS conversations (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id            TEXT NOT NULL,                  -- S2 会话归属校验
  created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, -- C4 乐观锁 / M7 超时清理
  deleted_at         DATETIME,                       -- K5 软删；非空视为已删除
  summary            TEXT,                           -- M2 早期对话滚动摘要
  summary_upto_msg_id INTEGER,                       -- M2 摘要覆盖到哪条消息
  active_flow        TEXT,                           -- M4 refund / price_protection / NULL
  pending_question   TEXT,                           -- M4 order_id / confirm / NULL
  draft              TEXT                            -- M4 JSON：已收集的要素
);

-- 消息：完整对话记录，用于回放、审计、窗口构建
CREATE TABLE IF NOT EXISTS messages (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id   INTEGER NOT NULL REFERENCES conversations(id),
  role              TEXT NOT NULL,                   -- user / assistant / tool
  content           TEXT,                            -- 文本；tool 消息可空
  tool_calls        TEXT,                            -- JSON：assistant 发起的工具调用
  tool_call_id      TEXT,                            -- tool 消息对应的调用 id
  status            TEXT,                            -- C1 completed / cancelled / error
  client_message_id TEXT,                            -- K4 幂等键（重试时复用）
  meta              TEXT,                            -- JSON：intent / route / citation_ids
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);
-- K4：部分索引，只对带幂等键的消息去重（多个 NULL 不算冲突）
CREATE UNIQUE INDEX IF NOT EXISTS uk_msg_idem
  ON messages(conversation_id, client_message_id)
  WHERE client_message_id IS NOT NULL;

-- 工单：转人工时创建；request_id 保证一次请求只建一张
CREATE TABLE IF NOT EXISTS tickets (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id   INTEGER NOT NULL,                -- 来自哪个会话
  order_id          TEXT,                            -- 关联订单（咨询类可空）
  ticket_type       TEXT,                            -- 投诉 / 退款 / 咨询
  description       TEXT,                            -- 用户原话或摘要
  request_id        TEXT NOT NULL,                   -- D11 幂等键
  status            TEXT NOT NULL DEFAULT 'open',    -- open / processing / closed
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_ticket_idem ON tickets(request_id);

-- 调用链：一次请求内每个节点/工具一行（对齐 OpenTelemetry span 模型）
CREATE TABLE IF NOT EXISTS trace_span (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id          TEXT NOT NULL,                   -- L7 与日志中的 trace_id 一致
  span_id           TEXT NOT NULL,                   -- 单个节点/工具调用 id
  parent_span_id    TEXT,                            -- 父 span，用于还原调用树
  name              TEXT NOT NULL,                   -- node:compute / tool:query_order
  start_at          DATETIME NOT NULL,
  end_at            DATETIME,                        -- NULL = 未结束或异常中断
  attrs             TEXT                             -- JSON：tokens / 结果摘要 / 错误码
);
CREATE INDEX IF NOT EXISTS idx_span_trace ON trace_span(trace_id);

-- 飞轮写回的知识：审核通过的低置信问题沉淀成的问答条目
CREATE TABLE IF NOT EXISTS faq (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  question           TEXT NOT NULL,                  -- 标准问题
  answer             TEXT NOT NULL,                  -- 审核后的答案
  source_type        TEXT NOT NULL,                  -- K5 flywheel / manual
  source_question_id INTEGER,                        -- K5 来自哪条低置信问题
  reviewed_at        DATETIME,                       -- K5 审核时间；NULL = 未经人工确认
  kb_version         TEXT,                           -- K6 写入时的知识库版本哈希
  sync_status        TEXT NOT NULL DEFAULT 'pending',-- K1 pending / synced
  created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 低置信池：飞轮入口，记录「系统答不好」的问题与当时证据
CREATE TABLE IF NOT EXISTS low_conf_questions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id   INTEGER,                         -- 来自哪个会话（回放用）
  question          TEXT NOT NULL,                   -- 用户原话
  normalized        TEXT,                            -- 归一化后的标准问题（合并同类用）
  source            TEXT,                            -- retrieval_low_conf / feedback / rule_conflict
  reason            TEXT,                            -- 判为低置信的原因（分数、守卫）
  retrieved         TEXT,                            -- JSON 三态：[...] / [] / NULL
  status            TEXT NOT NULL DEFAULT 'pending', -- pending / merged / approved / rejected / written_back
  merged_into       INTEGER,                         -- 合并到哪一条
  occurrence        INTEGER NOT NULL DEFAULT 1,      -- 出现次数（合并时累加）
  kb_ref            TEXT,                            -- 写回后的 FAQ 条目 id
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at       DATETIME                         -- 审核时间；驳回即终审，不复活
);

-- ============ 业务表（落表而非 mock 函数：归属校验要在 SQL 层完成）============

-- 订单：归属校验的载体，也是退费计算的输入
CREATE TABLE IF NOT EXISTS orders (
  id                TEXT PRIMARY KEY,                -- 订单号，如 1002
  user_id           TEXT NOT NULL,                   -- S1 归属人
  sku_id            TEXT NOT NULL,                   -- 商品型号，如 ST-60
  spec              TEXT,                            -- 规格
  status            TEXT NOT NULL,                   -- 待付款/待发货/已发货/已签收/退款中/已关闭
  amount            INTEGER NOT NULL,                -- 实付金额（分）
  created_at        DATETIME NOT NULL,               -- 下单时间；价保从这天起算
  paid_at           DATETIME,                        -- 支付时间
  shipped_at        DATETIME,                        -- 发货时间；决定能否改地址/取消
  tracking_no       TEXT                             -- 物流单号
);
CREATE INDEX IF NOT EXISTS idx_order_user ON orders(user_id);

-- 物流：B 出口查询轨迹
CREATE TABLE IF NOT EXISTS logistics (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tracking_no       TEXT NOT NULL,                   -- 物流单号
  order_id          TEXT NOT NULL REFERENCES orders(id),
  latest_status     TEXT NOT NULL,                   -- 运输中 / 已签收 / 异常
  nodes             TEXT,                            -- JSON：[{time,status,desc}]
  updated_at        DATETIME
);
CREATE INDEX IF NOT EXISTS idx_logistics_no ON logistics(tracking_no);

-- 库存与价格：B 出口答「有没有货」「多少钱」
CREATE TABLE IF NOT EXISTS stock (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  sku_id            TEXT NOT NULL,                   -- 商品型号
  spec              TEXT NOT NULL,                   -- 规格
  stock             INTEGER NOT NULL DEFAULT 0,      -- 可售库存
  price             INTEGER NOT NULL,                -- 当前售价（分）
  status            TEXT NOT NULL DEFAULT 'on_sale', -- on_sale / sold_out / presale
  UNIQUE(sku_id, spec)                               -- 型号+规格唯一
);

-- 优惠券：门槛与过期判断
CREATE TABLE IF NOT EXISTS coupons (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id           TEXT NOT NULL,                   -- 持券人
  code              TEXT NOT NULL,                   -- 券码
  threshold         INTEGER NOT NULL DEFAULT 0,      -- 使用门槛（分），0=无门槛
  discount          INTEGER NOT NULL,                -- 优惠金额（分）
  expires_at        DATETIME NOT NULL,               -- 过期时间
  used              INTEGER NOT NULL DEFAULT 0       -- 0 未使用 / 1 已使用
);
CREATE INDEX IF NOT EXISTS idx_coupon_user ON coupons(user_id);
