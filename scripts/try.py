"""手动试跑工具：每个 Task 完成后用它亲手验一遍行为。

**为什么单独做一个**：`pytest` 验证"符合预期"，这个工具验证"你想看的那句话"。
跟着做任务时，改一句 prompt 就想立刻看效果，用这个比加用例快。

用法：

    uv run python scripts/try.py intent "我买的推车能退吗"
    uv run python scripts/try.py graph  "我买的推车能退吗"

    uv run python scripts/try.py intent          # 不带文本 → 交互模式，逐行输入

后续任务会往这里加子命令（retrieve / gate / chat …），保持单一入口。
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/try.py` 直接跑

from app.graph.routing import FALLBACK_ROUTE, INTENT_TO_ROUTE


async def run_intent(text: str) -> None:
    """只跑意图分类：看它判成什么、置信度多少、会分流到哪个出口。"""
    from app.graph.nodes import classify

    intent, confidence, multi_intent = await classify(text)
    route = INTENT_TO_ROUTE.get(intent, FALLBACK_ROUTE)
    print(f"  intent={intent}  confidence={confidence:.2f}  route={route}")
    if multi_intent:
        print("  multi_intent=True  ← 这句话还有别的诉求（Ch05 会据此让用户二选一）")


async def run_graph(text: str) -> None:
    """跑完整主图：看整轮的结果（意图 → 路由 → 出口 → 回答）。"""
    from app.graph.build import build_graph

    result = await build_graph().ainvoke({"raw_query": text})
    print(f"  resolved={result.get('resolved_query')!r}")
    print(f"  intent={result.get('intent')}  confidence={result.get('intent_confidence', 0):.2f}")
    print(f"  route={result.get('route')}  placeholder={result.get('is_placeholder')}")
    print(f"  answer={result.get('answer')}")
    if result.get("actions"):
        print(f"  actions={result['actions']}")


async def run_retrieve(text: str, strategy: str | None = None) -> None:
    """只跑检索：看 top5 命中、相似度与排名，用于排查"为什么这条没召回到"。"""
    from app.config import get_settings
    from app.core.retrieval import search

    chosen = strategy or get_settings().retrieval_strategy
    hits = await search(text, top_k=5, strategy=chosen)  # type: ignore[arg-type]
    print(f"  strategy={chosen}")
    for rank, hit in enumerate(hits, start=1):
        print(
            f"  {rank}. {hit['id']:<34} "
            f"similarity={hit['similarity']:.3f}  rrf={hit['rrf_score']:.4f}"
        )


MODES = {
    "intent": run_intent,
    "graph": run_graph,
    "retrieve": run_retrieve,
}


async def main() -> int:
    parser = argparse.ArgumentParser(description="手动试跑（跟着 task 验收用）")
    parser.add_argument("mode", choices=sorted(MODES), help="跑哪一层")
    parser.add_argument("text", nargs="*", help="要试的句子；不传则进入交互模式")
    parser.add_argument(
        "-s", "--strategy", choices=("dense", "bm25", "hybrid"),
        help="仅 retrieve 模式：指定检索策略（默认取配置）",
    )
    args = parser.parse_args()

    if args.text:
        text = " ".join(args.text)
        print(f"[{args.mode}] {text!r}")
        if args.mode == "retrieve":
            await run_retrieve(text, args.strategy)
        else:
            await MODES[args.mode](text)
        return 0

    # 交互模式：逐行读，空行退出
    print(f"[{args.mode}] 交互模式，输入句子回车查看结果，空行退出。")
    for line in sys.stdin:
        text = line.strip()
        if not text:
            break
        print(f"> {text!r}")
        if args.mode == "retrieve":
            await run_retrieve(text, args.strategy)
        else:
            await MODES[args.mode](text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
