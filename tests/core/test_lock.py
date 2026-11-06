"""会话级锁（M8）：同会话串行、超时 busy、不同会话互不影响。"""
import asyncio

import pytest

from app.core import lock as lock_mod
from app.core.lock import BusyError, conversation_lock


async def test_same_conversation_is_serialized():
    entered = asyncio.Event()
    events: list[str] = []

    async def first():
        async with conversation_lock(1):
            entered.set()
            await asyncio.sleep(0.05)
            events.append("first-out")

    async def second():
        await entered.wait()
        async with conversation_lock(1, timeout=1):
            events.append("second-in")

    await asyncio.gather(first(), second())

    assert events == ["first-out", "second-in"]


async def test_wait_timeout_raises_busy():
    entered = asyncio.Event()

    async def first():
        async with conversation_lock(1):
            entered.set()
            await asyncio.sleep(0.2)

    async def second():
        await entered.wait()
        with pytest.raises(BusyError):
            async with conversation_lock(1, timeout=0.01):
                pass

    await asyncio.gather(first(), second())


async def test_different_conversations_are_independent():
    release = asyncio.Event()

    async def first():
        async with conversation_lock(1):
            await release.wait()

    async def second():
        async with conversation_lock(2, timeout=0.2):
            release.set()

    await asyncio.wait_for(asyncio.gather(first(), second()), timeout=1)


async def test_lock_registry_is_cleaned_up():
    async with conversation_lock(7):
        assert 7 in lock_mod._locks

    assert 7 not in lock_mod._locks


async def test_can_reacquire_after_release():
    async with conversation_lock(8):
        pass
    async with conversation_lock(8, timeout=0.1):
        pass
