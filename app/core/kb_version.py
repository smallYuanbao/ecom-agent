"""知识库内容哈希（K6）。

用途有两个：

1. **判可比性**：评测报告里带上 kb_version，改过知识库之后的指标不能和之前直接比。
2. **判是否需要重建**：`build_kb.py` 跑完打印它，两次一致说明语料没变。

算法：把所有 chunk 的 `(id, text)` 按 id 排序后拼接，取 sha256 前 12 位。

**为什么要排序**：`load_chunks()` 已经排好序了，但哈希不能依赖调用方的顺序——
排序是哈希稳定的前提，放在这里再排一次是有意的防御。
"""
import hashlib
from functools import lru_cache

from app.kb.loader import load_chunks


def kb_version(chunks: list[dict] | None = None) -> str:
    """返回知识库内容哈希（12 位十六进制）。"""
    items = chunks if chunks is not None else load_chunks()
    payload = "\n".join(
        f"{chunk['id']}\t{chunk['text']}" for chunk in sorted(items, key=lambda c: c["id"])
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@lru_cache
def cached_kb_version() -> str:
    """给 `/api/meta` 用：每次请求都读 7 个文件没必要，内容不变时哈希也不变。

    语料改了要重启服务（或清缓存）才会更新——这是有意的：
    kb_version 是"当前进程在用的知识库"，不是"磁盘上现在的样子"。
    """
    return kb_version()
