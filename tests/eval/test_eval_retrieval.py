"""困难检索集与阈值对比辅助函数。

重点不是跑检索（那要连 Chroma），而是守住两条：
1. 每道题的 gold id 必须真的在语料里——写错一个 id，hit@3 会静默变成 0；
2. `--set hard` 的查询数/题数与声明一致，避免「评测集悄悄缩水」。
"""
from app.core.eval_set import load_queries
from app.kb.loader import load_chunks
from scripts.eval_retrieval import SETS, discordance, wilson_interval


def test_hard_set_shape():
    queries = load_queries(SETS["hard"])
    assert len(queries) == 35
    assert len({q.case_id for q in queries}) == 25
    assert all(q.style in ("formal", "colloquial", "multi") for q in queries)


def test_hard_set_gold_ids_exist_in_corpus():
    corpus_ids = {chunk["id"] for chunk in load_chunks()}
    for query in load_queries(SETS["hard"]):
        missing = [expect for expect in query.expect_ids if expect not in corpus_ids]
        assert not missing, f"{query.query}: gold id 不在语料里 {missing}"


def test_hard_set_has_rare_tokens():
    """守一下设计意图：困难集必须真的含型号/法条号，不能退化成普通问法。"""
    text = " ".join(q.query for q in load_queries(SETS["hard"]))
    for token in ("ST-20", "CS-04", "FB-30", "第七条", "西藏", "专用发票"):
        assert token in text


def test_wilson_interval_edges():
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(35, 35)
    assert low < 1.0 and high == 1.0


def test_discordance_counts_only_disagreements():
    base = {"ranks": [1, 2, None, 4]}
    other = {"ranks": [1, None, 3, None]}
    assert discordance(base, other) == (1, 1)
