"""会话级锁（M8）：同一 `conversation_id` 同时只允许一轮执行。

**单进程有效**。多 worker / 多实例要换 Redis 锁或数据库 advisory lock；
当前部署是单进程，所以先用进程内锁把「状态互相覆盖」这条诡异 bug 挡住。

锁表按「事件循环 + 会话」存：pytest 每个用例一个循环，
不区分循环会把上一轮循环的 `asyncio.Lock` 带进来，报 "bound to a different event loop"。
"""
import asyncio
import threading
from contextlib import asynccontextmanager


class BusyError(Exception):
    """同一会话已有请求在处理，等待超时。"""


# cid -> (loop, lock, refcount)
_locks: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Lock, int]] = {}

# 只保护字典本身，临界区是微秒级；用 threading.Lock 避免它绑定到某个事件循环
_registry = threading.Lock()


def _entry(cid: int, loop: asyncio.AbstractEventLoop) -> tuple[asyncio.Lock, int]:
    """取该会话在当前循环下的锁，并把引用计数 +1。"""
    with _registry:
        entry = _locks.get(cid)
        if entry is None or entry[0] is not loop:
            lock = asyncio.Lock()
            refs = 1
        else:
            _, lock, refs = entry
            refs += 1
        _locks[cid] = (loop, lock, refs)
        return lock, refs


def _release(cid: int, loop: asyncio.AbstractEventLoop) -> None:
    """引用计数 -1；归零时移除，避免锁表无限增长。"""
    with _registry:
        entry = _locks.get(cid)
        if entry is None or entry[0] is not loop:
            return
        _, lock, refs = entry
        if refs <= 1:
            _locks.pop(cid, None)
        else:
            _locks[cid] = (loop, lock, refs - 1)


@asynccontextmanager
async def conversation_lock(cid: int, *, timeout: float = 5.0):
    """按会话串行；等待超过 `timeout` 抛 `BusyError`。

    锁必须覆盖整个流式输出：客户端断开时生成器关闭，`finally` 会释放。
    """
    loop = asyncio.get_running_loop()
    lock, _ = _entry(cid, loop)

    acquired = False
    try:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            acquired = True
        except TimeoutError as exc:
            raise BusyError(f"conversation {cid} is busy") from exc
        yield
    finally:
        if acquired:
            lock.release()
        _release(cid, loop)
