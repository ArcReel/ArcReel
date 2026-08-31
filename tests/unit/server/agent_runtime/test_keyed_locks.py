"""KeyedLocks — 按键弱引用锁注册表。"""

from __future__ import annotations

import asyncio
import gc

from server.agent_runtime.keyed_locks import KeyedLocks


class TestKeyedLocks:
    async def test_same_key_shares_lock_instance(self):
        locks = KeyedLocks()
        a = locks.lock_for("k1")
        b = locks.lock_for("k1")
        assert a is b

    async def test_different_keys_get_independent_locks(self):
        locks = KeyedLocks()
        assert locks.lock_for("k1") is not locks.lock_for("k2")

    async def test_lock_serializes_concurrent_holders(self):
        locks = KeyedLocks()
        order: list[str] = []

        async def _critical(tag: str) -> None:
            async with locks.lock_for("k"):
                order.append(f"{tag}-in")
                await asyncio.sleep(0)
                order.append(f"{tag}-out")

        await asyncio.gather(_critical("a"), _critical("b"))
        assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])

    async def test_unreferenced_lock_is_collected(self):
        """无协程持有/等待时锁对象自动回收，不为每个键永久驻留内存。"""
        locks = KeyedLocks()
        locks.lock_for("k1")
        gc.collect()
        assert len(locks) == 0
        assert "k1" not in locks

    async def test_discard_clears_registry_entry(self):
        locks = KeyedLocks()
        held = locks.lock_for("k1")
        assert "k1" in locks
        locks.discard("k1")
        assert "k1" not in locks
        # 已持有的锁对象仍可正常使用
        async with held:
            pass
        # discard 后再取拿到新实例
        assert locks.lock_for("k1") is not held
