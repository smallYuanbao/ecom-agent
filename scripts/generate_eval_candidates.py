"""调用外部大模型生成评测候选 query（不生成 gold）。

设计依据：`data/eval/generation/README.md`。

用法：

    export EVAL_GEN_API_KEY=...
    export EVAL_GEN_MODEL=...
    export EVAL_GEN_BASE_URL=https://api.openai.com/v1   # 兼容 OpenAI 协议的都可
    uv run python scripts/generate_eval_candidates.py --batch A
    uv run python scripts/generate_eval_candidates.py --batch B,C --count 25
    uv run python scripts/generate_eval_candidates.py --all --dry-run

输出：

    data/eval/generation/raw/<model>_<batch>.jsonl

**这个脚本只负责「用户会怎么说」**：期望金额、路由、资格仍由规则引擎和人工确认。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from openai import OpenAI

from app.core.eval_v1 import load_items

GENERATION_DIR = ROOT / "data" / "eval" / "generation"
DEFAULT_FACT = GENERATION_DIR / "fact_card.md"
DEFAULT_PROMPTS = GENERATION_DIR / "prompts.yaml"
DEFAULT_OUT = GENERATION_DIR / "raw"
DEFAULT_ENV_FILE = ROOT / ".env"

STYLES = frozenset({"formal", "colloquial", "multi"})
MAX_QUERY_CHARS = 300


@dataclass(frozen=True)
class GenConfig:
    api_key: str
    model: str
    base_url: str | None
    timeout: float
    retries: int
    temperature: float


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def load_env_file(path: Path) -> int:
    """把 `.env` 读进 `os.environ`，**不覆盖**已经存在的变量。

    只做最小解析：`KEY=VALUE`、`export KEY=VALUE`、引号、行尾注释。
    不引入额外依赖；命令行里显式 export 的变量优先于 `.env`。
    """
    if not path.exists():
        return 0
    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def resolve_models(args: argparse.Namespace) -> list[str]:
    """支持单模型 `--model` 和多模型 `--models` / `EVAL_GEN_MODELS`。"""
    raw = (
        args.models
        or args.model
        or _env("EVAL_GEN_MODELS", "EVAL_GEN_MODEL", "EVAL_GEN_API_NAME")
        or ""
    )
    models: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        model = item.strip()
        if model and model not in seen:
            models.append(model)
            seen.add(model)
    if not models:
        raise SystemExit("缺少模型：用 EVAL_GEN_MODELS / EVAL_GEN_MODEL 或 --models")
    return models


def resolve_config(args: argparse.Namespace, model: str) -> GenConfig:
    """API key / base URL 多个模型共用；命令行参数可覆盖 base URL。"""
    api_key = _env("EVAL_GEN_API_KEY", "EVAL_GEN_KEY")
    base_url = args.base_url or _env("EVAL_GEN_BASE_URL", "EVAL_GEN_API_BASE")

    if not api_key:
        raise SystemExit("缺少 EVAL_GEN_API_KEY（或 EVAL_GEN_KEY）")

    return GenConfig(
        api_key=api_key,
        model=model,
        base_url=base_url or None,
        timeout=float(_env("EVAL_GEN_TIMEOUT") or 120),
        retries=int(_env("EVAL_GEN_MAX_RETRIES") or 2),
        temperature=float(args.temperature),
    )


def load_prompts(path: Path = DEFAULT_PROMPTS) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw.get("system") or not raw.get("batches"):
        raise SystemExit(f"{path} 缺少 system 或 batches")
    return raw


def render_messages(fact_card: str, system: str, prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system.strip()},
        {
            "role": "user",
            "content": f"{fact_card.strip()}\n\n---\n\n{prompt.strip()}",
        },
    ]


def _temperature_unsupported(exc: Exception) -> bool:
    message = str(exc).lower()
    return "temperature" in message and (
        "not supported" in message or "unsupported" in message
    )


def create_completion(
    client: OpenAI, config: GenConfig, messages: list[dict[str, str]]
):
    """有些模型（如 Kimi K3）不接受 temperature；去掉该参数再试一次。"""
    try:
        return client.chat.completions.create(
            model=config.model,
            messages=messages,
            temperature=config.temperature,
        )
    except Exception as exc:
        if not _temperature_unsupported(exc):
            raise
        return client.chat.completions.create(
            model=config.model,
            messages=messages,
        )


def _strip_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:jsonl|json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_candidates(content: str) -> list[dict[str, Any]]:
    """兼容 JSON 数组、JSONL、带 ``` 围栏的输出。"""
    text = _strip_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        data = data.get("items") or [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or line in {"[", "]"}:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def clean_candidates(
    items: list[dict[str, Any]],
    *,
    existing: set[str],
    count: int,
) -> list[dict[str, str]]:
    """过滤空值、非法 style、重复和已有样本；最多保留 `count` 条。"""
    cleaned: list[dict[str, str]] = []
    seen = set(existing)

    for item in items:
        query = str(item.get("query") or "").strip()
        if not query or len(query) > MAX_QUERY_CHARS:
            continue
        key = _normalize(query)
        if key in seen:
            continue

        style = str(item.get("style") or "formal")
        if style not in STYLES:
            style = "formal"

        cleaned.append(
            {
                "query": query,
                "style": style,
                "scenario": str(item.get("scenario") or "").strip()[:80],
            }
        )
        seen.add(key)
        if len(cleaned) >= count:
            break
    return cleaned


def existing_queries() -> set[str]:
    return {_normalize(item.query) for item in load_items()}


def slugify_model(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-").lower() or "model"


def write_jsonl(rows: list[dict[str, str]], path: Path, *, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({**row, "model": model}, ensure_ascii=False) + "\n")


def select_batches(raw: dict[str, Any], requested: str) -> dict[str, Any]:
    batches = raw["batches"]
    if requested == "all":
        return batches
    wanted = [name.strip().upper() for name in requested.split(",") if name.strip()]
    unknown = [name for name in wanted if name not in batches]
    if unknown:
        raise SystemExit(f"未知批次 {unknown}；可选 {sorted(batches)}")
    return {name: batches[name] for name in wanted}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Ch07 评测候选 query")
    parser.add_argument(
        "--batch",
        default="all",
        help="批次字母，逗号分隔；默认 all（A-F）",
    )
    parser.add_argument("--all", action="store_true", help="等价于 --batch all")
    parser.add_argument("--count", type=int, help="覆盖 prompt 里的数量")
    parser.add_argument("--model", help="只跑一个模型（覆盖 EVAL_GEN_MODEL）")
    parser.add_argument(
        "--models",
        help="跑多个模型，逗号分隔（覆盖 EVAL_GEN_MODELS）",
    )
    parser.add_argument("--base-url", help="覆盖 EVAL_GEN_BASE_URL")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true", help="覆盖已存在的输出文件")
    parser.add_argument("--dry-run", action="store_true", help="只渲染 prompt，不调用模型")
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--fact-card", type=Path, default=DEFAULT_FACT)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="读取共享的 API key / base URL / 模型列表（默认仓库根 .env）",
    )
    args = parser.parse_args()

    loaded = load_env_file(args.env_file)
    if loaded:
        print(f"从 {args.env_file} 读取 {loaded} 个变量（已有环境变量优先）")

    models = resolve_models(args)
    fact_card = args.fact_card.read_text(encoding="utf-8")
    raw_prompts = load_prompts(args.prompts)
    batches = select_batches(raw_prompts, "all" if args.all else args.batch)

    if args.dry_run:
        print(f"模型：{', '.join(models)}")
        for name, batch in batches.items():
            messages = render_messages(fact_card, raw_prompts["system"], batch["prompt"])
            print(f"[{name}] {batch['title']}  system={len(messages[0]['content'])} 字")
        return 0

    first = resolve_config(args, models[0])
    client = OpenAI(
        api_key=first.api_key,
        base_url=first.base_url,
        timeout=first.timeout,
        max_retries=first.retries,
    )
    known = existing_queries()
    failures = 0

    for model in models:
        config = resolve_config(args, model)
        print(f"\n=== 模型 {model} ===")
        for name, batch in batches.items():
            out_path = args.out_dir / f"{slugify_model(config.model)}_{name}.jsonl"
            if out_path.exists() and not args.force:
                print(f"[{name}] 已存在，跳过：{out_path}（--force 覆盖）")
                continue

            count = args.count or int(batch["count"])
            messages = render_messages(fact_card, raw_prompts["system"], batch["prompt"])
            print(f"[{name}] {batch['title']} → 请求 {count} 条")

            try:
                response = create_completion(client, config, messages)
                content = response.choices[0].message.content or ""
            except Exception as exc:                   # noqa: BLE001
                print(f"[{name}] 调用失败：{exc}")
                failures += 1
                continue

            parsed = parse_candidates(content)
            cleaned = clean_candidates(parsed, existing=known, count=count)
            if not cleaned:
                print(f"[{name}] 没有可用的候选（原始 {len(parsed)} 条）")
                failures += 1
                continue

            write_jsonl(cleaned, out_path, model=config.model)
            known.update(_normalize(row["query"]) for row in cleaned)
            print(f"[{name}] 写入 {len(cleaned)}/{count} 条 → {out_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
