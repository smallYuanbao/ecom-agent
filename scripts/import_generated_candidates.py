"""把网页模型（Gemini / ChatGPT 等）手工复制的输出清洗成 raw JSONL。

网页对话不会直接给文件，常见输出是：

    ```json
    [{"query": "...", "style": "formal"}]
    ```

或者每行一个 JSON。这个脚本复用生成脚本的解析/清洗逻辑，
去掉代码围栏、过滤重复和空值，再按 `<model>_<batch>.jsonl` 落盘。

用法：

    uv run python scripts/import_generated_candidates.py \
      --input data/eval/generation/manual/gemini_D.txt \
      --model gemini-web-2026-11-08 \
      --batch D

如果分几次粘贴，用 `--append` 追加到同一个文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.generate_eval_candidates import (
    DEFAULT_OUT,
    clean_candidates,
    existing_queries,
    parse_candidates,
    slugify_model,
)

DEFAULT_MANUAL = ROOT / "data" / "eval" / "generation" / "manual"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _existing_in_file(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        query = str(item.get("query") or "").strip()
        if query:
            keys.add(_normalize(query))
    return keys


def import_text(
    text: str,
    *,
    model: str,
    batch: str | None,
    count: int,
    out_dir: Path,
    append: bool,
    overwrite: bool,
) -> tuple[Path, int]:
    """解析并写盘；返回 (输出路径, 新增条数)。"""
    suffix = batch.upper() if batch else "manual"
    out_path = out_dir / f"{slugify_model(model)}_{suffix}.jsonl"

    if out_path.exists() and not append and not overwrite:
        raise SystemExit(f"{out_path} 已存在；用 --append 追加或 --force 覆盖")

    known = existing_queries()
    known |= _existing_in_file(out_path)
    cleaned = clean_candidates(parse_candidates(text), existing=known, count=count)
    if not cleaned:
        raise SystemExit("没有可用的候选（检查是否只有解释文字，没有 JSON）")

    out_dir.mkdir(parents=True, exist_ok=True)
    mode = "a" if append and out_path.exists() else "w"
    with out_path.open(mode, encoding="utf-8") as handle:
        for row in cleaned:
            handle.write(
                json.dumps({**row, "model": model}, ensure_ascii=False) + "\n"
            )
    return out_path, len(cleaned)


def main() -> int:
    parser = argparse.ArgumentParser(description="导入网页模型手工复制的候选")
    parser.add_argument("--input", type=Path, required=True, help="复制的原始文本")
    parser.add_argument("--model", required=True, help="模型标签，如 gemini-web-2026-11-08")
    parser.add_argument("--batch", help="批次字母（A-F）；不填则记为 manual")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--append", action="store_true", help="追加到已有文件")
    parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"输入文件不存在：{args.input}")

    out_path, added = import_text(
        args.input.read_text(encoding="utf-8"),
        model=args.model,
        batch=args.batch,
        count=args.count,
        out_dir=args.out_dir,
        append=args.append,
        overwrite=args.force,
    )
    print(f"写入 {added} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
