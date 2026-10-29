"""检索：分词、融合、距离换算（单元）+ 真实检索（集成）。

单元测试不依赖任何服务；集成测试需要本地 Chroma(:8000) 与 Ollama(:11434)。
"""
import httpx
import pytest

from app.core.retrieval import cosine, rrf_fuse, search, tokenize


def _services_up() -> bool:
    try:
        httpx.get("http://localhost:8000/api/v2/heartbeat", timeout=2).raise_for_status()
        httpx.get("http://localhost:11434/api/tags", timeout=2).raise_for_status()
        return True
    except Exception:  # noqa: BLE001 — 探活失败一律跳过
        return False


requires_services = pytest.mark.skipif(
    not _services_up(), reason="需要本地 Chroma(:8000) 与 Ollama(:11434)"
)


# ── 单元：分词 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "must_have"),
    [
        ("ST-20 能带上飞机吗", "st-20"),      # 型号必须整体保留
        ("CS-04L 加长款", "cs-04l"),          # 带后缀字母的型号
        ("满 99 元包邮", "99"),               # 数字单独成词
        ("退货运费由谁承担", "运费"),          # 领域词不被切碎
    ],
)
def test_tokenize_keeps_key_terms(text: str, must_have: str):
    assert must_have in tokenize(text), f"{text!r} 分词后丢了 {must_have!r}"


def test_tokenize_does_not_duplicate_code_digits():
    """`st-20` 里的 20 不能既作为型号一部分、又作为独立数字再算一次。"""
    tokens = tokenize("ST-20")
    assert tokens.count("20") <= 1, tokens


# ── 单元：融合与相似度 ────────────────────────────────────────


def test_cosine_of_identical_vectors_is_one():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_rrf_rewards_chunks_found_by_both_channels():
    """两路都排第一 > 只有一路排第一——这是 RRF 存在的全部理由。"""
    dense = [{"id": "a", "text": "", "source": "", "section": "", "similarity": 0.9},
             {"id": "b", "text": "", "source": "", "section": "", "similarity": 0.5}]
    sparse = [{"id": "b", "text": "", "source": "", "section": ""},
              {"id": "c", "text": "", "source": "", "section": ""}]

    fused = rrf_fuse(dense, sparse, k=60, top_k=3)
    assert fused[0]["id"] == "b"          # 两路都出现，分数最高
    scores = {h["id"]: h["rrf_score"] for h in fused}
    assert scores["b"] > scores["a"] > scores["c"]


def test_rrf_score_is_far_below_evidence_threshold():
    """守卫：RRF 分数与证据闸阈值不是一个量纲。

    `1/(60+1) ≈ 0.016`，而阈值是 0.45。谁要是把 rrf_score 丢给闸比较，
    闸会永远不通过——知识出口全部走兜底。
    """
    hits = [{"id": "x", "text": "", "source": "", "section": ""}]
    fused = rrf_fuse(hits, hits, k=60, top_k=1)      # 两路都排第一 = 最大值
    assert fused[0]["rrf_score"] < 0.05


# ── 集成：真实检索 ──────────────────────────────────────────


@requires_services
async def test_dense_finds_the_obvious_chunk():
    hits = await search("退货运费谁承担", top_k=3, strategy="dense")
    assert hits[0]["id"] == "returns#退货运费由谁承担"
    assert 0.0 <= hits[0]["similarity"] <= 1.0


@requires_services
async def test_bm25_finds_model_numbers():
    """型号是 BM25 的主场：字面精确匹配。"""
    hits = await search("FB-30 保修多久", top_k=3, strategy="bm25")
    assert "products#辅食机 FB-30" in [h["id"] for h in hits]


@requires_services
@pytest.mark.parametrize("strategy", ["dense", "bm25", "hybrid"])
async def test_every_strategy_returns_contract_fields(strategy: str):
    hits = await search("包邮门槛是多少", top_k=5, strategy=strategy)
    assert hits, f"{strategy} 没有召回任何结果"
    for hit in hits:
        assert {"id", "text", "source", "section", "rrf_score", "similarity"} <= set(hit)
        # 闸只认 similarity，所以它必须存在且是 0–1 的相似度（不是距离）
        assert 0.0 <= hit["similarity"] <= 1.0


@requires_services
async def test_bm25_only_hits_still_get_similarity():
    """BM25 单独召回的块也要补上 similarity，否则闸无法判定。"""
    hits = await search("FB-30 保修多久", top_k=5, strategy="bm25")
    assert all("similarity" in hit for hit in hits)
