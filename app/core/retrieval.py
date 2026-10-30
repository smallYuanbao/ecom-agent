"""检索：dense（Chroma+Ollama）、sparse（BM25）、RRF 融合。

三条必须遵守的约定（见 docs/reports/local-env.md）：

1. **Chroma 返回的是 distance，不是相似度**——`similarity = 1 - distance`。
   证据闸比较的是 similarity；直接比 distance 会让"越不相关越容易通过"。
2. **RRF 分数只用于排序**，量纲是 `1/(k+rank)`，最大值约 0.033，**不要拿去比阈值**。
3. 集合必须是 cosine 空间。建错集合不会报错，只会让上面第 1 条失效。

## 分词为什么要定制

jieba 默认把「退货运费」切成 `退 / 货运费`，把 `ST-20` 切成 `ST / - / 20`。
对 BM25 来说这是硬伤——型号、条号、金额都是**字面精确匹配**的场景。

所以 `tokenize()` 做两件事：

- 先用正则把**型号与数字**单独摘出来（`st-20`、`art-006`、`99`、`12`），不经过 jieba
- 剩下的文本用 `lcut_for_search`（会额外产出子词，召回更全）+ 领域自定义词
"""
import math
import re
from functools import lru_cache
from typing import Any, Literal

import httpx
import jieba
from rank_bm25 import BM25Okapi

from app.config import get_settings
from app.kb.loader import load_chunks

Strategy = Literal["dense", "bm25", "hybrid"]

# 型号（ST-20 / CS-04L / art-006 的 art 部分）与独立数字
_CODE_RE = re.compile(r"[a-z]+-\d+[a-z]?", re.IGNORECASE)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 领域词：**直接抽取，不交给 jieba**。
#
# 原因：jieba 遇到「多少钱包邮」会切成 `多少 / 钱包 / 邮`——「钱包」把「包邮」吃掉了。
# `add_word` 和 `suggest_freq` 都压不住（实测过），因为这是最大概率路径问题。
# 所以这里先按字面整词抽出来，剩下的文本再交给 jieba。
_DOMAIN_TERMS = tuple(
    sorted(
        (
            "运费险", "无理由", "包邮", "运费", "退货", "退款", "价保", "补差价",
            "订单变更", "辅食机", "安全座椅", "婴儿推车", "洗碗机", "保修",
            "七天", "赠品", "发票", "会员", "积分", "优惠券", "叠加",
        ),
        key=len,
        reverse=True,  # 长词优先，避免「运费险」被「运费」抢先匹配
    )
)

# 停用词：不滤掉的话，「多少钱包邮」会靠「多少」「钱」命中
# `本文档不提供什么` 这类短块，把真正相关的 `包邮门槛` 压到第 4 名。
# 只放纯功能词与通用疑问词，不动任何可能承载语义的词。
_STOPWORDS = frozenset(
    "的 了 吗 呢 吧 啊 是 有 能 可以 怎么 怎样 什么 多少 哪 哪个 我 你 您 他 这 那 就 要 "
    "会 在 和 与 及 也 都 很 更 最 个 一个 一下 请问 想问 帮我 看看".split()
)

_collection_id: str | None = None


# ── 分词 ──────────────────────────────────────────────────────


def tokenize(text: str) -> list[str]:
    """切词。型号走正则整体保留，数字单独摘出，其余走 jieba。

    先摘型号再找数字——否则 `st-20` 里的 `20` 会被重复计一次，
    让型号类查询的 BM25 分数虚高。
    """
    lowered = text.lower()
    codes = _CODE_RE.findall(lowered)
    residue = _CODE_RE.sub(" ", lowered)

    terms: list[str] = []
    for term in _DOMAIN_TERMS:
        if term in residue:
            terms.append(term)
            residue = residue.replace(term, " ")

    numbers = _NUM_RE.findall(residue)
    residue = _NUM_RE.sub(" ", residue)
    words = [t for t in jieba.lcut_for_search(residue) if t.strip() and t not in _STOPWORDS]
    return codes + terms + numbers + words


# ── 基础设施 ──────────────────────────────────────────────────


def _chroma_url() -> str:
    settings = get_settings()
    return (
        f"http://{settings.chroma_host}:{settings.chroma_port}/api/v2"
        f"/tenants/default_tenant/databases/default_database"
    )


async def _post(client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(_chroma_url() + path, json=body)
    response.raise_for_status()
    return response.json()


async def _get_collection_id(client: httpx.AsyncClient) -> str:
    """按名字解析集合 id 并缓存——id 是 UUID，不能靠名字拼 URL。"""
    global _collection_id
    if _collection_id is not None:
        return _collection_id
    name = get_settings().chroma_collection
    response = await client.get(_chroma_url() + "/collections")
    response.raise_for_status()
    for item in response.json():
        if item["name"] == name:
            _collection_id = item["id"]
            return _collection_id
    raise RuntimeError(f"集合 {name} 不存在，先跑 `uv run python scripts/build_kb.py`")


async def embed(texts: list[str]) -> list[list[float]]:
    """调 Ollama 取向量（bge-m3，1024 维）。"""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{settings.ollama_base_url}/api/embed",
            json={"model": settings.embed_model, "input": texts},
        )
        response.raise_for_status()
        return response.json()["embeddings"]


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度。Ollama 返回的向量未保证归一化，这里自己算。"""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


@lru_cache
def _bm25() -> tuple[BM25Okapi, tuple[dict, ...]]:
    """BM25 索引与语料。语料小（40 块），启动时全量构建即可。"""
    chunks = tuple(load_chunks())
    return BM25Okapi([tokenize(c["text"]) for c in chunks]), chunks


# ── 三种检索 ──────────────────────────────────────────────────


async def dense_search(
    query: str, *, k: int, vector: list[float] | None = None
) -> list[dict]:
    """向量检索。返回的每条都带 similarity（= 1 - distance）。

    可传入已算好的 `vector`——hybrid 分支要复用同一次 embedding，避免算两遍。
    """
    if vector is None:
        vector = (await embed([query]))[0]
    async with httpx.AsyncClient(timeout=60.0) as client:
        collection_id = await _get_collection_id(client)
        result = await _post(
            client,
            f"/collections/{collection_id}/query",
            {
                "query_embeddings": [vector],
                "n_results": k,
                "include": ["documents", "metadatas", "distances"],
            },
        )

    hits: list[dict] = []
    for i, chunk_id in enumerate(result["ids"][0]):
        meta = result["metadatas"][0][i] or {}
        hits.append(
            {
                "id": chunk_id,
                "text": result["documents"][0][i],
                "source": meta.get("source", ""),
                "section": meta.get("section", ""),
                # Ch02 spec：闸比较 similarity，不比较 distance
                "similarity": 1.0 - float(result["distances"][0][i]),
            }
        )
    return hits


def bm25_search(query: str, *, k: int) -> list[dict]:
    """关键词检索。返回顺序即排名，不含相似度（BM25 分数无界，不能当阈值用）。"""
    bm25, chunks = _bm25()
    scores = bm25.get_scores(tokenize(query))
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:k]
    return [
        {
            "id": chunks[i]["id"],
            "text": chunks[i]["text"],
            "source": chunks[i]["source"],
            "section": chunks[i]["section"],
        }
        for i in ranked
        if scores[i] > 0
    ]


def rrf_fuse(*ranked_lists: list[dict], k: int = 60, top_k: int = 5) -> list[dict]:
    """Reciprocal Rank Fusion：`score = Σ 1/(k + rank)`，rank 从 1 开始。

    只用排名、不看原始分数，所以不需要处理"BM25 无界 vs 余弦 0–1"的归一化问题——
    这正是当初选 RRF 而不是线性加权的原因。
    """
    fused: dict[str, dict] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            entry = fused.setdefault(hit["id"], {**hit, "rrf_score": 0.0})
            entry["rrf_score"] += 1.0 / (k + rank)
            # 相似度只可能来自 dense；BM25 单独召回的先留空，由调用方补
            if "similarity" in hit and "similarity" not in entry:
                entry["similarity"] = hit["similarity"]

    ordered = sorted(fused.values(), key=lambda h: h["rrf_score"], reverse=True)[:top_k]
    return ordered


async def _fill_missing_similarity(hits: list[dict], vector: list[float]) -> None:
    """给 BM25 单独召回的块补 similarity。

    为什么非补不可：证据闸只认 similarity。补不上的块无法判定，
    要么一律放行（危险）要么一律拒绝（丢召回），都不对。
    """
    missing = [h for h in hits if "similarity" not in h]
    if not missing:
        return

    async with httpx.AsyncClient(timeout=60.0) as client:
        collection_id = await _get_collection_id(client)
        result = await _post(
            client,
            f"/collections/{collection_id}/get",
            {"ids": [h["id"] for h in missing], "include": ["embeddings"]},
        )
    vectors = {cid: emb for cid, emb in zip(result["ids"], result["embeddings"], strict=True)}
    for hit in missing:
        emb = vectors.get(hit["id"])
        hit["similarity"] = cosine(vector, emb) if emb else 0.0


async def search(query: str, *, top_k: int = 5, strategy: Strategy | None = None) -> list[dict]:
    """统一检索入口。返回 `[{id, text, source, section, rrf_score, similarity}]`。

    `rrf_score` 仅供排序与调试，**不要拿去和阈值比较**。
    """
    settings = get_settings()
    chosen: Strategy = strategy or settings.retrieval_strategy  # type: ignore[assignment]

    if chosen == "bm25":
        hits = bm25_search(query, k=settings.sparse_top_k)[:top_k]
        vector = (await embed([query]))[0]
        await _fill_missing_similarity(hits, vector)
        for rank, hit in enumerate(hits, start=1):
            hit["rrf_score"] = 1.0 / (settings.rrf_k + rank)
        return hits

    if chosen == "dense":
        hits = await dense_search(query, k=top_k)
        for rank, hit in enumerate(hits, start=1):
            hit["rrf_score"] = 1.0 / (settings.rrf_k + rank)
        return hits

    # hybrid：两路各取 candidate 条，融合后取 top_k。embedding 只算一次
    vector = (await embed([query]))[0]
    dense_hits = await dense_search(query, k=settings.dense_top_k, vector=vector)
    sparse_hits = bm25_search(query, k=settings.sparse_top_k)
    fused = rrf_fuse(dense_hits, sparse_hits, k=settings.rrf_k, top_k=top_k)
    await _fill_missing_similarity(fused, vector)
    return fused
