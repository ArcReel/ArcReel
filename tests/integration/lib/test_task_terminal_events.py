"""任务终态经 project_events 通道发布的测试。

覆盖两层：`build_task_terminal_change` 的纯转换，以及 GenerationQueue 各终态入口
（成功 / 失败 / 取消 / 批量取消 / 级联失败）落库后是否把事件发上 batch 总线。
"""

import pytest

from lib.db.repositories.task_repo import TaskRepository
from lib.generation_queue import GenerationQueue
from lib.project_change_hints import register_project_change_batch_listener
from lib.task_terminal_events import build_task_terminal_change


@pytest.fixture
async def queue(db_factory):
    return GenerationQueue(session_factory=db_factory)


@pytest.fixture
def captured_batches():
    """收集本测试期间发上 batch 总线的 (project_name, changes)。"""
    batches: list[tuple[str, list[dict]]] = []

    def listener(project_name, _source, changes):
        batches.append((project_name, [dict(c) for c in changes]))

    unregister = register_project_change_batch_listener(listener)
    yield batches
    unregister()


def _task_changes(batches):
    """只取任务终态变更，忽略同期其它项目变更。"""
    return [c for _, changes in batches for c in changes if c["entity_type"] == "task"]


class TestBuildTaskTerminalChange:
    @pytest.mark.parametrize(
        ("status", "action"),
        [("succeeded", "task_succeeded"), ("failed", "task_failed"), ("cancelled", "task_cancelled")],
    )
    def test_terminal_statuses_map_to_actions(self, status, action):
        change = build_task_terminal_change({"task_id": "t1", "status": status, "task_type": "video"})
        assert change is not None
        assert change["action"] == action
        assert change["entity_type"] == "task"
        assert change["entity_id"] == "t1"
        assert change["task_type"] == "video"

    def test_refresh_signal_not_user_facing_notification(self):
        """终态变更只用于触发刷新：不弹通知、不聚焦跳转。"""
        change = build_task_terminal_change({"task_id": "t1", "status": "succeeded"})
        assert change is not None
        assert change["important"] is False
        assert change["focus"] is None

    @pytest.mark.parametrize(
        "event",
        [
            {"task_id": "t1", "status": "queued"},
            {"task_id": "t1", "status": "running"},
            {"task_id": "t1", "status": "cancelling"},
            {"task_id": "", "status": "succeeded"},
        ],
    )
    def test_non_terminal_or_invalid_yields_none(self, event):
        assert build_task_terminal_change(event) is None


class TestQueueEmitsTerminalEvents:
    async def _enqueue_running(self, queue, resource_id="E1S01", task_type="video"):
        task = await queue.enqueue_task(
            project_name="demo",
            task_type=task_type,
            media_type="video",
            resource_id=resource_id,
            payload={},
            script_file="episode_01.json",
            source="webui",
        )
        await queue.claim_next_task(media_type="video")
        return task["task_id"]

    async def test_success_emits_task_succeeded(self, queue, captured_batches):
        task_id = await self._enqueue_running(queue)
        captured_batches.clear()

        await queue.mark_task_succeeded(task_id, {"file_path": "videos/E1S01.mp4"})

        changes = _task_changes(captured_batches)
        assert [c["action"] for c in changes] == ["task_succeeded"]
        assert changes[0]["entity_id"] == task_id
        assert changes[0]["task_type"] == "video"
        assert captured_batches[0][0] == "demo"

    async def test_failure_emits_task_failed(self, queue, captured_batches):
        task_id = await self._enqueue_running(queue)
        captured_batches.clear()

        await queue.mark_task_failed(task_id, "provider timeout")

        assert [c["action"] for c in _task_changes(captured_batches)] == ["task_failed"]

    async def test_cancel_emits_task_cancelled(self, queue, captured_batches):
        task = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S02",
            payload={},
            script_file="episode_01.json",
            source="webui",
        )
        captured_batches.clear()

        await queue.cancel_task(task["task_id"])

        changes = _task_changes(captured_batches)
        assert [c["action"] for c in changes] == ["task_cancelled"]
        assert changes[0]["entity_id"] == task["task_id"]

    async def test_cancel_all_queued_emits_one_batch_per_project(self, queue, captured_batches):
        for idx in range(3):
            await queue.enqueue_task(
                project_name="demo",
                task_type="video",
                media_type="video",
                resource_id=f"E1S1{idx}",
                payload={},
                script_file="episode_01.json",
                source="webui",
            )
        captured_batches.clear()

        await queue.cancel_all_queued("demo")

        # 三条终态合并进同一批，避免每任务一次快照重建。
        assert len(captured_batches) == 1
        changes = _task_changes(captured_batches)
        assert len(changes) == 3
        assert {c["action"] for c in changes} == {"task_cancelled"}

    async def test_non_terminal_transitions_emit_nothing(self, queue, captured_batches):
        """入队与领取只是中间态，不该触发终态事件。"""
        await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S03",
            payload={},
            script_file="episode_01.json",
            source="webui",
        )
        await queue.claim_next_task(media_type="video")

        assert _task_changes(captured_batches) == []

    async def test_no_publish_when_body_raises_before_commit(self, queue, captured_batches, monkeypatch):
        """终态已收集但会话未提交时不得发布——没有终态落库就没有终态可通告。

        `_task_repo` 把发布放在 `async with` 之后正是为此；若发布挪进 repo 内部或提到
        提交之前，前端会收到一条对应状态并未落库的终态事件。
        """
        task_id = await self._enqueue_running(queue)
        captured_batches.clear()

        original_record = TaskRepository._record_terminal_event

        def raise_after_collecting(self, **kwargs):
            original_record(self, **kwargs)
            assert self.terminal_events, "前置条件：终态已被 _record_terminal_event 收集"
            raise RuntimeError("commit 前炸了")

        monkeypatch.setattr(TaskRepository, "_record_terminal_event", raise_after_collecting)

        with pytest.raises(RuntimeError, match="commit 前炸了"):
            await queue.mark_task_succeeded(task_id, {"file_path": "videos/E1S01.mp4"})

        assert _task_changes(captured_batches) == []

    async def test_cascade_failure_emits_event_for_dependent(self, queue, captured_batches):
        """级联失败的下游任务同样进事件——终态收口在 _record_terminal_event，一处覆盖全路径。"""
        parent = await queue.enqueue_task(
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S04",
            payload={},
            script_file="episode_01.json",
            source="webui",
        )
        child = await queue.enqueue_task(
            project_name="demo",
            task_type="video",
            media_type="video",
            resource_id="E1S04",
            payload={},
            script_file="episode_01.json",
            source="webui",
            dependency_task_id=parent["task_id"],
        )
        await queue.claim_next_task(media_type="image")
        captured_batches.clear()

        await queue.mark_task_failed(parent["task_id"], "boom")

        failed_ids = {c["entity_id"] for c in _task_changes(captured_batches) if c["action"] == "task_failed"}
        assert failed_ids == {parent["task_id"], child["task_id"]}
