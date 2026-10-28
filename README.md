# 电商客服 Agent

面向消费者的售后客服（C 端），核心是把退费核算从人工翻合同变成可核对、可追溯的自动计算。

> 设计阶段已完成，代码实现中。设计文档见 [docs/design/](docs/design/)。

## 快速开始

```bash
uv sync
uv run python scripts/init_db.py     # 建 10 张表（幂等，并校验 schema.sql 与 ORM 一致）
uv run python scripts/seed.py        # 灌合成数据：6 SKU / 20 订单 / 5 券
uv run pytest -q                     # 30 条测试
uv run uvicorn app.main:app --reload # :8000，看 /api/health
```

本章不需要任何模型密钥（Ch01 零 LLM 调用）。

## 架构

```
意图分流（12 意图 / 5 出口）
  ├─ A 知识：检索 → 证据闸 → 生成
  ├─ B 数据：参数校验 → 工具 → 结果校验
  ├─ C 规则：8 状态流水线（抽取 → 校验 → 查数 → 计算 → 合规 → 应答）
  ├─ D 转人工
  └─ E 兜底
```

## 设计取舍

- **金额由代码算，模型只复述** —— 合同规则有 6 种组合，模型算不出可复现的结果
- **追问用独立状态而非 interrupt** —— 需要可单测、图上看得到
- **守卫失败按不通过处理（fail-closed）** —— 判断不了 ≠ 通过
- **数据类问题不进向量检索** —— 能查库的不检索

## 局限

- 无真实用户，数据为合成
- 未接支付与真实物流，退款为模拟
- 评测集规模有限

## License

待定
