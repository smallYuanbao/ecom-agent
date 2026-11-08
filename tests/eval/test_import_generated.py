"""网页模型手工输出导入：解析、去重、追加与覆盖。"""
import json

import pytest

from scripts.import_generated_candidates import import_text

TEXT = """```jsonl
{"query": "网页生成的问题", "style": "colloquial", "scenario": "口语"}
```"""


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_import_manual_text(tmp_path):
    path, added = import_text(
        TEXT,
        model="gemini-web",
        batch="D",
        count=10,
        out_dir=tmp_path,
        append=False,
        overwrite=False,
    )

    assert added == 1
    assert path.name == "gemini-web_D.jsonl"
    assert _lines(path) == [
        {"query": "网页生成的问题", "style": "colloquial", "scenario": "口语", "model": "gemini-web"}
    ]


def test_import_skips_existing_eval_query(tmp_path):
    text = "\n".join(
        [
            json.dumps({"query": "退货政策是什么"}, ensure_ascii=False),
            json.dumps({"query": "网页生成的新问题"}, ensure_ascii=False),
        ]
    )

    path, added = import_text(
        text,
        model="gemini-web",
        batch="A",
        count=10,
        out_dir=tmp_path,
        append=False,
        overwrite=False,
    )

    assert added == 1
    assert [row["query"] for row in _lines(path)] == ["网页生成的新问题"]


def test_import_append(tmp_path):
    first = json.dumps({"query": "第一条"}, ensure_ascii=False)
    second = json.dumps({"query": "第二条"}, ensure_ascii=False)

    path, _ = import_text(
        first,
        model="gemini-web",
        batch="D",
        count=10,
        out_dir=tmp_path,
        append=False,
        overwrite=False,
    )
    import_text(
        second,
        model="gemini-web",
        batch="D",
        count=10,
        out_dir=tmp_path,
        append=True,
        overwrite=False,
    )

    assert [row["query"] for row in _lines(path)] == ["第一条", "第二条"]


def test_import_refuses_overwrite(tmp_path):
    path = tmp_path / "gemini-web_D.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit, match="已存在"):
        import_text(
            TEXT,
            model="gemini-web",
            batch="D",
            count=10,
            out_dir=tmp_path,
            append=False,
            overwrite=False,
        )
