"""语料切分与版本哈希。

不测"内容写得对不对"（那是人读的），只测**切分规则与哈希的稳定性**——
这两样一旦跑偏，下游检索和评测全部失真，而且不会报错。
"""
import pytest

from app.core.kb_version import kb_version
from app.kb.loader import cn_to_int, load_chunks, split_sections

EXPECTED_ARTICLE_NOS = {6, 7, 8, 10, 12}
DOC_SOURCES = {
    "returns.md",
    "shipping.md",
    "invoice.md",
    "member.md",
    "promo.md",
    "products.md",
}


def test_all_six_docs_are_loaded():
    sources = {c["source"] for c in load_chunks() if c["kind"] == "doc"}
    assert sources == DOC_SOURCES


def test_all_five_articles_are_loaded():
    regulations = [c for c in load_chunks() if c["kind"] == "regulation"]
    assert {c["article_no"] for c in regulations} == EXPECTED_ARTICLE_NOS
    assert {c["id"] for c in regulations} == {"art-006", "art-007", "art-008", "art-010", "art-012"}


def test_chunk_ids_are_unique():
    ids = [c["id"] for c in load_chunks()]
    assert len(ids) == len(set(ids)), "chunk id 重复会让 upsert 互相覆盖"


def test_every_chunk_has_title_in_text():
    """标题拼进 text 是有意的：像「本文档不提供什么」这种小节，没有标题根本看不懂。"""
    for chunk in load_chunks():
        assert chunk["text"].startswith(chunk["section"]), chunk["id"]


def test_no_chunk_is_empty():
    assert all(c["text"].strip() for c in load_chunks())


def test_chunks_are_sorted_by_id():
    ids = [c["id"] for c in load_chunks()]
    assert ids == sorted(ids), "顺序不稳定会让 kb_version 每次都不一样"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("六", 6), ("八", 8), ("十", 10), ("十二", 12), ("二十", 20), ("二十三", 23)],
)
def test_cn_to_int(text: str, expected: int):
    assert cn_to_int(text) == expected


def test_cn_to_int_rejects_unknown():
    with pytest.raises(ValueError):
        cn_to_int("零")


def test_split_sections_ignores_h1_and_header_block():
    """`#` 标题与它下面的说明段不属于任何小节，不能变成 chunk。"""
    markdown = "# 文档名\n\n> 说明段\n\n## 第一节\n\n正文一\n\n## 第二节\n\n正文二\n"
    assert split_sections(markdown) == [("第一节", "正文一"), ("第二节", "正文二")]


def test_kb_version_is_deterministic():
    assert kb_version() == kb_version()


def test_kb_version_changes_with_content():
    """改一个字，哈希就要变——否则评测报告会拿不可比的指标做对比（K6）。"""
    chunks = load_chunks()
    baseline = kb_version(chunks)

    mutated = [dict(c) for c in chunks]
    mutated[0]["text"] += "改一个字"
    assert kb_version(mutated) != baseline


def test_kb_version_is_twelve_hex_chars():
    version = kb_version()
    assert len(version) == 12
    assert all(ch in "0123456789abcdef" for ch in version)
