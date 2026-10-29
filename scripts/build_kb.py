"""切分 + 向量化 + 入库。

流程：读语料 → 算 kb_version → 调 Ollama 批量取向量 → upsert 到 Chroma → 删掉多余 id。

**三条必须遵守的约束**（见 docs/reports/local-env.md）：

1. 集合名 `ecom_kb`，不复用已有的 `rag_*`（维度/空间不匹配，还会掺入母婴数据）
2. 建集合时**显式 `space=cosine`**——默认是 l2，返回欧氏距离，"越小越相似"，
   与证据闸"相似度越高越可信"的语义相反
3. Chroma 返回的是 **distance**，查询侧要用 `similarity = 1 - distance`

**为什么要删除多余 id**：只 upsert 的话，从文档里删掉的小节会永远留在库里，
检索引到一段已经不存在的内容。语料是权威，库要跟着它走。
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/build_kb.py` 直接跑

import httpx

from app.config import get_settings
from app.core.kb_version import kb_version
from app.kb.loader import load_chunks

TENANT = "default_tenant"
DATABASE = "default_database"
EMBED_BATCH = 16          # 一次请求送多少条去向量化
TIMEOUT = 120.0


def chroma_base() -> str:
    settings = get_settings()
    return f"http://{settings.chroma_host}:{settings.chroma_port}/api/v2"


def collections_url() -> str:
    return f"{chroma_base()}/tenants/{TENANT}/databases/{DATABASE}/collections"


async def ensure_collection(client: httpx.AsyncClient) -> str:
    """取或建集合，并校验空间确实是 cosine。返回 collection id。"""
    name = get_settings().chroma_collection
    response = await client.post(
        collections_url(),
        json={
            "name": name,
            "configuration": {"hnsw": {"space": "cosine"}},
            "get_or_create": True,
        },
    )
    response.raise_for_status()
    collection = response.json()

    space = (collection.get("configuration_json") or {}).get("hnsw", {}).get("space")
    if space != "cosine":
        raise SystemExit(
            f"集合 {name} 的空间是 {space!r}，不是 cosine。\n"
            "证据闸依赖 cosine 相似度，用 l2 距离会让语义反转。\n"
            f"请先删除该集合再重跑：curl -X DELETE {collections_url()}/{name}"
        )
    return collection["id"]


async def embed_all(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """调 Ollama 批量取向量（bge-m3，1024 维）。"""
    settings = get_settings()
    url = f"{settings.ollama_base_url}/api/embed"
    vectors: list[list[float]] = []

    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        response = await client.post(
            url, json={"model": settings.embed_model, "input": batch}, timeout=TIMEOUT
        )
        response.raise_for_status()
        vectors.extend(response.json()["embeddings"])
        print(f"  向量化 {min(start + EMBED_BATCH, len(texts))}/{len(texts)}", end="\r")

    print()
    return vectors


async def existing_ids(client: httpx.AsyncClient, collection_id: str) -> set[str]:
    response = await client.post(
        f"{collections_url()}/{collection_id}/get", json={"include": []}
    )
    response.raise_for_status()
    return set(response.json().get("ids") or [])


async def main() -> int:
    chunks = load_chunks()
    if not chunks:
        print("没有读到任何 chunk，检查 data/kb/ 下是否有 .md")
        return 1

    version = kb_version(chunks)
    settings = get_settings()
    print(f"读到 {len(chunks)} 个 chunk，kb_version={version}")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        collection_id = await ensure_collection(client)
        embeddings = await embed_all(client, [c["text"] for c in chunks])

        response = await client.post(
            f"{collections_url()}/{collection_id}/upsert",
            json={
                "ids": [c["id"] for c in chunks],
                "embeddings": embeddings,
                "documents": [c["text"] for c in chunks],
                "metadatas": [
                    {
                        "source": c["source"],
                        "section": c["section"],
                        "kind": c["kind"],
                        "kb_version": version,
                        **({"article_no": c["article_no"]} if "article_no" in c else {}),
                    }
                    for c in chunks
                ],
            },
        )
        response.raise_for_status()

        stale = await existing_ids(client, collection_id) - {c["id"] for c in chunks}
        if stale:
            delete = await client.post(
                f"{collections_url()}/{collection_id}/delete", json={"ids": sorted(stale)}
            )
            delete.raise_for_status()
            print(f"删除已不在语料中的 {len(stale)} 个旧 chunk：{sorted(stale)}")

    print(f"集合 {settings.chroma_collection} 已更新")
    print(f"chunks={len(chunks)}  kb_version={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
