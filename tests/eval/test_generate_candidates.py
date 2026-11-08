"""外部生成脚本：解析、清洗、配置与批次选择（不联网）。"""
import argparse
import json
import os
from types import SimpleNamespace

import pytest

from scripts.generate_eval_candidates import (
    GenConfig,
    _temperature_unsupported,
    clean_candidates,
    create_completion,
    load_env_file,
    load_prompts,
    parse_candidates,
    render_messages,
    resolve_config,
    resolve_models,
    select_batches,
    slugify_model,
)


def _args(**overrides) -> argparse.Namespace:
    base = {
        "model": None,
        "models": None,
        "base_url": None,
        "temperature": 0.8,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_parse_jsonl():
    content = "\n".join(
        [
            json.dumps({"query": "退货政策是什么", "style": "formal"}, ensure_ascii=False),
            json.dumps({"query": "运费谁出", "style": "colloquial"}, ensure_ascii=False),
        ]
    )

    items = parse_candidates(content)

    assert [item["query"] for item in items] == ["退货政策是什么", "运费谁出"]


def test_parse_fenced_json_array():
    content = '```json\n[{"query": "ST-20 有货吗"}]\n```'

    assert parse_candidates(content) == [{"query": "ST-20 有货吗"}]


def test_parse_ignores_non_json_lines():
    content = '说明：\n{"query": "能退吗"}\n不是 JSON'

    assert parse_candidates(content) == [{"query": "能退吗"}]


def test_clean_dedupes_against_existing_and_itself():
    existing = {"退货政策是什么"}
    items = [
        {"query": "退货政策是什么", "style": "formal"},
        {"query": "退货政策是什么？", "style": "formal"},
        {"query": "我要退款", "style": "colloquial", "scenario": "退款"},
    ]

    cleaned = clean_candidates(items, existing=existing, count=10)

    assert [row["query"] for row in cleaned] == ["退货政策是什么？", "我要退款"]
    assert cleaned[1]["style"] == "colloquial"


def test_clean_filters_empty_long_and_bad_style():
    items = [
        {"query": ""},
        {"query": "长" * 301},
        {"query": "ST-20 多少钱", "style": "unknown"},
    ]

    cleaned = clean_candidates(items, existing=set(), count=10)

    assert cleaned == [{"query": "ST-20 多少钱", "style": "formal", "scenario": ""}]


def test_clean_respects_count():
    items = [{"query": f"问题{i}"} for i in range(20)]

    assert len(clean_candidates(items, existing=set(), count=5)) == 5


def test_render_messages_contains_fact_and_prompt():
    messages = render_messages("## 商品\nST-20", "你是用户", "生成 10 条")

    assert messages[0]["role"] == "system"
    assert "你是用户" in messages[0]["content"]
    assert "ST-20" in messages[1]["content"]
    assert "生成 10 条" in messages[1]["content"]


def test_slugify_model():
    assert slugify_model("Qwen/Qwen3-Max") == "qwen-qwen3-max"
    assert slugify_model("gpt-4.1") == "gpt-4.1"


def test_resolve_config_reads_env(monkeypatch):
    monkeypatch.setenv("EVAL_GEN_API_KEY", "sk-test")
    monkeypatch.setenv("EVAL_GEN_BASE_URL", "http://localhost:9999/v1")

    config = resolve_config(_args(), "test-model")

    assert config.api_key == "sk-test"
    assert config.model == "test-model"
    assert config.base_url == "http://localhost:9999/v1"


def test_resolve_config_requires_key(monkeypatch):
    monkeypatch.delenv("EVAL_GEN_API_KEY", raising=False)
    monkeypatch.delenv("EVAL_GEN_KEY", raising=False)

    with pytest.raises(SystemExit, match="EVAL_GEN_API_KEY"):
        resolve_config(_args(), "test-model")


def test_temperature_unsupported_detection():
    assert _temperature_unsupported(
        RuntimeError("Parameter 'temperature'=0.8 is not supported for kimi-k3 model.")
    )
    assert not _temperature_unsupported(RuntimeError("rate limit exceeded"))


def test_create_completion_retries_without_temperature():
    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if "temperature" in kwargs:
                raise RuntimeError("Parameter 'temperature' is not supported")
            return "ok"

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    config = GenConfig(
        api_key="k",
        model="kimi-k3",
        base_url=None,
        timeout=1,
        retries=0,
        temperature=0.8,
    )

    assert create_completion(client, config, []) == "ok"
    assert len(calls) == 2
    assert "temperature" not in calls[1]


def test_resolve_models_from_env_dedupes(monkeypatch):
    monkeypatch.setenv("EVAL_GEN_MODELS", "model-a, model-b,model-a")

    assert resolve_models(_args()) == ["model-a", "model-b"]


def test_resolve_models_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("EVAL_GEN_MODELS", "env-model")

    assert resolve_models(_args(models="cli-a,cli-b")) == ["cli-a", "cli-b"]


def test_resolve_models_requires_one(monkeypatch):
    monkeypatch.delenv("EVAL_GEN_MODELS", raising=False)
    monkeypatch.delenv("EVAL_GEN_MODEL", raising=False)
    monkeypatch.delenv("EVAL_GEN_API_NAME", raising=False)

    with pytest.raises(SystemExit, match="缺少模型"):
        resolve_models(_args())


def test_load_env_file_does_not_override(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EVAL_TEST_KEEP=from-file\n"
        "export EVAL_TEST_QUOTED='a b'\n"
        "EVAL_TEST_COMMENT=value # 注释\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(os, "environ", {"EVAL_TEST_KEEP": "from-shell"})

    loaded = load_env_file(env_file)

    assert loaded == 2
    assert os.environ["EVAL_TEST_KEEP"] == "from-shell"
    assert os.environ["EVAL_TEST_QUOTED"] == "a b"
    assert os.environ["EVAL_TEST_COMMENT"] == "value"


def test_load_prompts_has_all_batches():
    raw = load_prompts()

    assert set(raw["batches"]) == {"A", "B", "C", "D", "E", "F"}
    assert raw["batches"]["C"]["count"] == 25


def test_select_batches():
    raw = load_prompts()

    assert set(select_batches(raw, "A,C")) == {"A", "C"}
    assert set(select_batches(raw, "all")) == set(raw["batches"])
    with pytest.raises(SystemExit, match="未知批次"):
        select_batches(raw, "Z")
