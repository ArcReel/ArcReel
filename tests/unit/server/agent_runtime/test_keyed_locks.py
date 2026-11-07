"""KeyedLocks: weak-reference keyed lock registry."""

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
        """Release unheld, unawaited locks automatically rather than retaining memory forever per key."""
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
        # Already-held lock remains usable.
        async with held:
            pass
        # After discard, another lookup returns a new instance.
        assert locks.lock_for("k1") is not held
