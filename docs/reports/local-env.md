# 本地环境现状（2026-10-29 实测）

> 采集方式：`docker ps` / Chroma HTTP API v2 / `ollama list` / `curl :11434/api/embeddings`。
> 本文只记录**外部依赖的现状**，不是设计文档。变了就更新这里。

## 一、Chroma

| 项 | 值 |
|---|---|
| 镜像 | `chromadb/chroma:latest` |
| 版本 | **1.0.0**（`GET /api/v2/version`） |
| 运行方式 | Docker，容器名 `chroma` |
| 端口 | **8000**（`0.0.0.0:8000->8000`） |
| 已运行 | 约 2 周（与 evalflow-redis 同期启动） |

### 现有集合

| collection | 条数 | 维度 | space | 内容 | 归属 |
|---|---|---|---|---|---|
| `user_profiles` | 1 | 384 | l2 | 用户画像 | 其他项目 |
| `rag_samples` | 15 | 1024 | **cosine** | 母婴育儿 QA（AI 生成样例） | baby-ai |
| `rag_medical` | 3633 | 1024 | l2 | 医疗语料 | baby-ai |
| `rag_user_uploads` | 25 | 1024 | l2 | 用户上传文档 | baby-ai |
| `rag_docs` | 220 | 1024 | l2 | 母婴育儿 QA | baby-ai |

**没有电商客服语料**。本项目需要从零建 `ecom_kb`。

## 二、Ollama

| 项 | 值 |
|---|---|
| 端口 | 11434 |
| 模型 | `bge-m3:latest`（1.2 GB） |
| 维度 | **1024**（实测 `POST /api/embeddings` 返回 1024 个浮点） |

与 T3（BGE-M3 本地）一致，无需切换 API。

## 三、端口分配

| 服务 | 端口 | 是否可动 |
|---|---|---|
| Chroma（Docker） | **8000** | ❌ 不动——已运行 2 周，baby-ai 侧依赖 |
| **本项目 FastAPI** | **8080** | ✅ 原计划 8000，改到 8080 |
| Ollama | 11434 | ❌ 系统级服务 |

## 四、三条落地约束（不遵守会出 bug）

### ① 建集合必须显式 `space=cosine`

本地 5 个集合里 4 个是 l2（Chroma 默认），只有 `rag_samples` 是 cosine。

```python
client.get_or_create_collection(
    name="ecom_kb",
    configuration={"hnsw": {"space": "cosine"}},
)
```

**不指定的后果**：`query()` 返回欧氏距离，而证据闸的语义是"相似度越高越可信"——两者方向相反，闸会变成"越不相关越容易通过"。

### ② 集合名用 `ecom_kb`，不复用既有集合

| 风险 | 说明 |
|---|---|
| 维度不匹配 | `user_profiles` 是 384 维，写入 1024 维向量直接报错 |
| 检索掺入 | 复用 `rag_docs` 会把母婴内容召回到电商问答里 |
| 误删 | 重建索引时清空集合，会影响 baby-ai 与 evalflow |

### ③ Chroma 返回的是 `distance`，不是相似度

```
cosine distance = 1 - cosine similarity
⇒ similarity = 1 - distance
```

retrieval 层必须做这个换算，并把 `similarity` 字段交给证据闸；`rrf_score` 只用于排序。

## 五、隐私提醒

`rag_user_uploads` 中含一份**真实简历**（`filename` 里带真实姓名与手机号），内容为明文的个人经历与联系方式。

- 不影响本项目，但**导出 / 备份 / 演示这个 Chroma 实例前，先确认不连带这个集合**
- 本项目只写 `ecom_kb`，且全部为合成数据（6 SKU / 20 订单 / 5 券为虚构；法规为公开文本）

## 六、复核命令

```bash
docker ps --format '{{.Names}}\t{{.Ports}}'
curl -s localhost:8000/api/v2/version
curl -s "localhost:8000/api/v2/tenants/default_tenant/databases/default_database/collections" | jq -r '.[].name'
ollama list
curl -s localhost:11434/api/embeddings -d '{"model":"bge-m3","prompt":"test"}' | jq '.embedding | length'
```
