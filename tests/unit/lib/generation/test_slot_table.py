"""SlotTable 占用台账纯内存单测。

容量无关、被动数据结构：register / promote / release / has_room / drain_finished /
occupied_providers / active_task_ids / all_active_tasks。用 ``loop.create_future()`` 造 dummy 执行体。
"""

import asyncio

from lib.generation.generation_worker import SlotTable


def _pending_future() -> asyncio.Future:
    """未完成的 future（模拟在跑的 task）。"""
    return asyncio.get_running_loop().create_future()


def _done_future() -> asyncio.Future:
    """已完成的 future（模拟 done 的 task）。"""
    f = asyncio.get_running_loop().create_future()
    f.set_result(None)
    return f


class TestSlotTable:
    async def test_register_counts_toward_occupied_and_has_room(self):
        """① 容量记账：register → occupied / has_room（capacity 由 caller 传入）。"""
        st = SlotTable()
        st.register("p", "image", "t1", _pending_future())
        assert st.occupied("p", "image") == 1
        assert st.has_room("p", "image", 2) is True
        assert st.has_room("p", "image", 1) is False
        assert st.has_room("p", "image", 0) is False

    async def test_pending_counts_toward_capacity(self):
        """② pending 计入容量：pending=1, cap=1 → has_room False。"""
        st = SlotTable()
        st.register("p", "video", "t1", _pending_future(), pending=True)
        assert st.occupied("p", "video") == 1
        assert st.has_room("p", "video", 1) is False

    async def test_register_release_idempotent(self):
        """③ 登记/释放幂等：重复 register 覆盖、release 不存在 no-op、release 两次幂等。"""
        st = SlotTable()
        f1 = _pending_future()
        f2 = _pending_future()
        st.register("p", "image", "t1", f1)
        st.register("p", "image", "t1", f2)
        assert st.occupied("p", "image") == 1
        assert st.all_active_tasks() == [f2]
        st.release("p", "image", "ghost")
        assert st.occupied("p", "image") == 1
        st.release("p", "image", "t1")
        st.release("p", "image", "t1")
        assert st.occupied("p", "image") == 0
        f1.cancel()

    async def test_capacity_change_does_not_disturb_occupancy_grow(self):
        """④ 改配置不扰占用 3→5：占用容器不变，凭空多出空位。"""
        st = SlotTable()
        for i in range(3):
            st.register("p", "video", f"t{i}", _pending_future())
        assert st.has_room("p", "video", 3) is False
        assert st.has_room("p", "video", 5) is True
        assert st.occupied("p", "video") == 3

    async def test_capacity_shrink_tolerates_overflow_no_eviction(self):
        """⑤ 改配置不扰占用 3→1 超额容忍：no room 但绝不驱逐在跑的 3 个。"""
        st = SlotTable()
        for i in range(3):
            st.register("p", "video", f"t{i}", _pending_future())
        assert st.has_room("p", "video", 1) is False
        assert st.occupied("p", "video") == 3
        assert len(st.all_active_tasks()) == 3

    async def test_promote_flips_phase_without_changing_count(self):
        """⑥ promote pending→inflight：占用数不变；promote 前 done 的 pending 不被 drain、后才被。"""
        st = SlotTable()
        done = _done_future()
        st.register("p", "video", "t1", done, pending=True)
        assert st.drain_finished() == []
        assert st.occupied("p", "video") == 1
        st.promote("p", "video", "t1")
        assert st.occupied("p", "video") == 1
        drained = st.drain_finished()
        assert [tid for tid, _ in drained] == ["t1"]
        assert st.occupied("p", "video") == 0
        st.promote("p", "video", "ghost")

    async def test_drain_finished_only_done_inflight(self):
        """⑦ drain_finished 只返回 done 的 INFLIGHT，pending 与未完成保留。"""
        st = SlotTable()
        done_inflight = _done_future()
        running_inflight = _pending_future()
        done_pending = _done_future()
        st.register("p", "image", "done", done_inflight)
        st.register("p", "image", "running", running_inflight)
        st.register("p", "video", "queued", done_pending, pending=True)

        drained = dict(st.drain_finished())
        assert set(drained) == {"done"}
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
        """⑨ [关键] occupied_providers：按 media 分隔；释放/ drain 掉最后一个占用后不残留。

        这是池满黑名单决策的支点：空 bucket 必须被剪除，否则已清空的 provider 会
        永远出现在黑名单源里。
        """
        st = SlotTable()
        st.register("p-img", "image", "i1", _pending_future())
        st.register("p-vid", "video", "v1", _pending_future())
        st.register("p-both", "image", "i2", _pending_future())
        st.register("p-both", "video", "v2", _pending_future())

        assert st.occupied_providers("image") == {"p-img", "p-both"}
        assert st.occupied_providers("video") == {"p-vid", "p-both"}

        st.release("p-vid", "video", "v1")
        assert st.occupied_providers("video") == {"p-both"}

        st.release("p-both", "video", "v2")
        st.register("p-both", "video", "v3", _done_future())
        st.drain_finished()
        assert "p-both" not in st.occupied_providers("video")
        assert st.occupied_providers("image") == {"p-img", "p-both"}
