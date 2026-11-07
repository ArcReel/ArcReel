"""In-memory SlotTable occupancy tests.

Capacity-agnostic passive structure: register/promote/release/has_room/drain_finished/
occupied_providers/active_task_ids/all_active_tasks. Create dummy executions through ``loop.create_future()``.
"""

import asyncio

from lib.generation.generation_worker import SlotTable


def _pending_future() -> asyncio.Future:
    """Unfinished future simulating running tasks."""
    return asyncio.get_running_loop().create_future()


def _done_future() -> asyncio.Future:
    """Completed future simulating done tasks."""
    f = asyncio.get_running_loop().create_future()
    f.set_result(None)
    return f


class TestSlotTable:
    async def test_register_counts_toward_occupied_and_has_room(self):
        """① Capacity accounting: register → occupied/has_room, with capacity supplied by callers."""
        st = SlotTable()
        st.register("p", "image", "t1", _pending_future())
        assert st.occupied("p", "image") == 1
        assert st.has_room("p", "image", 2) is True
        assert st.has_room("p", "image", 1) is False
        # capacity<=0 always means no room, judging supplied values without owning capacity.
        assert st.has_room("p", "image", 0) is False

    async def test_pending_counts_toward_capacity(self):
        """② Pending counts toward capacity: pending=1/cap=1 means has_room False."""
        st = SlotTable()
        st.register("p", "video", "t1", _pending_future(), pending=True)
        assert st.occupied("p", "video") == 1
        assert st.has_room("p", "video", 1) is False

    async def test_register_release_idempotent(self):
        """③ Registration/release idempotency: duplicate register overwrites, missing release is no-op, repeated release idempotent."""
        st = SlotTable()
        f1 = _pending_future()
        f2 = _pending_future()
        st.register("p", "image", "t1", f1)
        st.register("p", "image", "t1", f2)  # Same-ID overwrite.
        assert st.occupied("p", "image") == 1
        assert st.all_active_tasks() == [f2]
        # Missing release → no-op.
        st.release("p", "image", "ghost")
        assert st.occupied("p", "image") == 1
        # Repeated release → idempotent.
        st.release("p", "image", "t1")
        st.release("p", "image", "t1")
        assert st.occupied("p", "image") == 0
        f1.cancel()

    async def test_capacity_change_does_not_disturb_occupancy_grow(self):
        """④ Capacity 3→5 leaves occupancy unchanged and simply adds room."""
        st = SlotTable()
        for i in range(3):
            st.register("p", "video", f"t{i}", _pending_future())
        assert st.has_room("p", "video", 3) is False
        # Changing 3→5 supplies another int; occupancy accounting stays unchanged.
        assert st.has_room("p", "video", 5) is True
        assert st.occupied("p", "video") == 3

    async def test_capacity_shrink_tolerates_overflow_no_eviction(self):
        """⑤ Capacity 3→1 tolerates overcommit: no room, never evict three running tasks."""
        st = SlotTable()
        for i in range(3):
            st.register("p", "video", f"t{i}", _pending_future())
        assert st.has_room("p", "video", 1) is False
        assert st.occupied("p", "video") == 3
        assert len(st.all_active_tasks()) == 3

    async def test_promote_flips_phase_without_changing_count(self):
        """⑥ pending→inflight promotion preserves counts; completed pending drains only after promotion."""
        st = SlotTable()
        done = _done_future()
        st.register("p", "video", "t1", done, pending=True)
        # Before promotion, completed PENDING is not drained.
        assert st.drain_finished() == []
        assert st.occupied("p", "video") == 1
        st.promote("p", "video", "t1")
        assert st.occupied("p", "video") == 1  # Flip flags only; count unchanged.
        # After promotion, completed INFLIGHT drains.
        drained = st.drain_finished()
        assert [tid for tid, _ in drained] == ["t1"]
        assert st.occupied("p", "video") == 0
        # Missing promotion → no-op.
        st.promote("p", "video", "ghost")

    async def test_drain_finished_only_done_inflight(self):
        """⑦ drain_finished returns completed INFLIGHT only; retain pending/unfinished entries."""
        st = SlotTable()
        done_inflight = _done_future()
        running_inflight = _pending_future()
        done_pending = _done_future()
        st.register("p", "image", "done", done_inflight)
        st.register("p", "image", "running", running_inflight)
        st.register("p", "video", "queued", done_pending, pending=True)

        drained = dict(st.drain_finished())
        assert set(drained) == {"done"}
        # Retain unfinished inflight and completed-but-still-PENDING occupancy.
        assert st.active_task_ids() == {"running", "queued"}
        assert set(st.all_active_tasks()) == {running_inflight, done_pending}
        running_inflight.cancel()

    async def test_active_views_and_clear(self):
        """⑧ active_task_ids / all_active_tasks（pending+inflight）/ clear。"""
        st = SlotTable()
        a = _pending_future()
        b = _pending_future()
        st.register("p", "image", "a", a)
        st.register("p", "video", "b", b, pending=True)
        assert st.active_task_ids() == {"a", "b"}
        assert set(st.all_active_tasks()) == {a, b}
        st.clear()
        assert st.active_task_ids() == set()
        assert st.all_active_tasks() == []
        a.cancel()
        b.cancel()

    async def test_occupied_providers_by_media_and_empty_bucket_pruned(self):
        """⑨ Critical occupied_providers separates media and leaves no entries after final release/drain.

        Full-pool blacklisting depends on pruning empty buckets; otherwise emptied providers
        remain forever in blacklist sources.
        """
        st = SlotTable()
        st.register("p-img", "image", "i1", _pending_future())
        st.register("p-vid", "video", "v1", _pending_future())
        st.register("p-both", "image", "i2", _pending_future())
        st.register("p-both", "video", "v2", _pending_future())

        # Separate media_type; image occupancy never appears in video sets.
        assert st.occupied_providers("image") == {"p-img", "p-both"}
        assert st.occupied_providers("video") == {"p-vid", "p-both"}

        # Release p-vid’s final video occupancy, pruning its empty bucket/provider.
        st.release("p-vid", "video", "v1")
        assert st.occupied_providers("video") == {"p-both"}

        # Drain also prunes empty buckets; replace p-both video with completed INFLIGHT.
        st.release("p-both", "video", "v2")
        st.register("p-both", "video", "v3", _done_future())
        st.drain_finished()
        assert "p-both" not in st.occupied_providers("video")
        # Image lane remains unaffected.
        assert st.occupied_providers("image") == {"p-img", "p-both"}
