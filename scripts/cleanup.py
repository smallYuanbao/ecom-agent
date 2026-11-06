"""清理过期的跨轮流程状态（M7）。

读时 `load_flow` 已经会惰性清空；这个脚本只做批量卫生，
避免长期不活跃的会话一直留着 `active_flow`。

用法：

    uv run python scripts/cleanup.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/cleanup.py` 直接跑

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.session_state import FLOW_TTL
from app.db.models import Conversation, utcnow
from app.db.session import session_scope


async def cleanup_expired(*, session: AsyncSession | None = None) -> int:
    """清空 `updated_at` 超过 TTL 且仍有流程的会话，返回清理条数。"""
    cutoff = utcnow() - FLOW_TTL
    async with session_scope(session) as s:
        stmt = select(Conversation).where(
            Conversation.active_flow.is_not(None),
            Conversation.updated_at < cutoff,
        )
        rows = list((await s.scalars(stmt)).all())
        for conv in rows:
            conv.active_flow = None
            conv.pending_question = None
            conv.draft = None
        return len(rows)


async def main() -> int:
    count = await cleanup_expired()
    print(f"已清理 {count} 个过期流程")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
