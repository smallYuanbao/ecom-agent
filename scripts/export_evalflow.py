"""把 `eval_v1.yaml` 导出成 EvalFlow 的扁平格式，可选直接导入。

EvalFlow 的 `DatasetItem` 只有三个字段：`input` / `expected_output` / `bucket`。
这里把 Ch07 的完整样本压平；`id` 保留在 JSON 里便于回溯，导入时忽略。

用法：

    uv run python scripts/export_evalflow.py
    uv run python scripts/export_evalflow.py --import --base-url http://localhost:8002
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from app.core.eval_v1 import EvalItem, load_items, validate_items

DEFAULT_OUT = ROOT / "data" / "eval" / "evalflow_v1.json"
DEFAULT_DATASET = "ecom-eval-v1"


def to_rows(items: list[EvalItem]) -> list[dict[str, str]]:
    """只保留 EvalFlow 认识的字段；`id` 是给报告回溯用的。"""
    return [
        {
            "id": item.id,
            "input": item.query,
            "expected_output": item.expected_output,
            "bucket": item.bucket,
        }
        for item in items
    ]


def write_json(rows: list[dict[str, str]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _find_dataset(client: httpx.Client, name: str) -> int | None:
    response = client.get("/datasets", params={"search": name, "page_size": 100})
    response.raise_for_status()
    for dataset in response.json().get("items") or []:
        if dataset.get("name") == name:
            return int(dataset["id"])
    return None


def import_rows(
    rows: list[dict[str, str]],
    *,
    base_url: str,
    dataset_name: str = DEFAULT_DATASET,
) -> tuple[int, int]:
    """导入到 EvalFlow；返回 (新增条数, 跳过条数)。"""
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30) as client:
        dataset_id = _find_dataset(client, dataset_name)
        if dataset_id is None:
            response = client.post(
                "/datasets",
                json={
                    "name": dataset_name,
                    "description": "Ch07 端到端评测集（由 eval_v1.yaml 导出）",
                },
            )
            response.raise_for_status()
            dataset_id = int(response.json()["id"])

        existing_response = client.get(f"/datasets/{dataset_id}/items")
        existing_response.raise_for_status()
        existing = {item["input"] for item in existing_response.json()}

        added = skipped = 0
        for row in rows:
            if row["input"] in existing:
                skipped += 1
                continue
            response = client.post(
                f"/datasets/{dataset_id}/items",
                json={
                    "input": row["input"],
                    "expected_output": row["expected_output"],
                    "bucket": row["bucket"],
                },
            )
            response.raise_for_status()
            existing.add(row["input"])
            added += 1
    return added, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 / 导入 Ch07 评测集")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--base-url", default="http://localhost:8002")
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    args = parser.parse_args()

    items = load_items()
    validate_items(items)
    rows = to_rows(items)
    write_json(rows, args.out)
    print(f"导出 {len(rows)} 条 → {args.out}")

    if args.do_import:
        added, skipped = import_rows(
            rows, base_url=args.base_url, dataset_name=args.dataset_name
        )
        print(f"导入 EvalFlow: 新增 {added} 条，跳过 {skipped} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
