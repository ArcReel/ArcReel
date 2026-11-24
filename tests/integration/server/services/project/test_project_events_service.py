import asyncio
import contextlib
import json
import logging
import threading

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lib.artifacts.artifact_activation import register_current_artifact, register_current_resource_artifact
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.edit_timeline import EditTimelineService
from lib.generation.generation_queue import GenerationQueue
from lib.project.project_change_hints import (
    emit_project_change_batch,
    emit_project_change_hint,
    project_change_source,
)
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_plan import WorkflowPlanRequest
from lib.workflow.workflow_state import WorkflowStateService, workflow_finished
from server.agent_toolset.edit_timelines import CREATE_TIMELINE
from server.dependencies import require_project_migration_ok
from server.routers import edit_timelines
from server.services.project.project_events import (
    ASSISTANT_SESSION_RESUMED_EVENT,
    PROJECT_DELETED_EVENT,
    ProjectEventService,
    read_project_state,
)
from server.services.project.project_state_projection import ProjectState
from server.services.project.workflow_planner import WorkflowPlanner
from tests.auth_deps import override_auth
from tests.factories import make_test_video
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


class _ControlledReader:
    """read_state seam 的测试 adapter：委托真实只读加载；设了 ``hook`` 后每次读盘经它放行。"""

    def __init__(self, pm: ProjectManager):
        self._pm = pm
        self.hook = None

    def __call__(self, project_name: str) -> ProjectState:
        def read() -> ProjectState:
            return read_project_state(self._pm, project_name)

        return read() if self.hook is None else self.hook(read)


def _pending_assets() -> dict:
    return {
        "storyboard_image": None,
        "video_clip": None,
        "video_uri": None,
        "status": "pending",
    }


def _terminal_task_change(task_id: str) -> dict:
    """任务终态事件：触发显式重建，且不描述任何文件侧实体变更。"""
    return {
        "entity_type": "task",
        "action": "task_succeeded",
        "entity_id": task_id,
        "label": task_id,
        "focus": None,
        "important": False,
        "task_type": "video",
    }


async def _next_event(stream, *, timeout: float) -> tuple[str, dict]:  # noqa: ASYNC109 -- 测试轮询 helper 的等待上限，非生产取消语义
    """Pull the next real (event_name, payload) tuple, skipping ``_idle`` sentinels."""

    async def _pull() -> tuple[str, dict]:
        async for item in stream:
            if isinstance(item, dict):
                if item.get("type") == "_idle":
                    continue
                raise AssertionError(f"unexpected dict sentinel: {item}")
            return item
        raise AssertionError("stream ended before a real event arrived")

    return await asyncio.wait_for(_pull(), timeout=timeout)


@pytest.mark.parametrize("entry", ["agent", "http"])
async def test_timeline_creation_refreshes_project_events_and_completes_workflow(tmp_path, db_factory, entry):
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo", "Demo", "", "ad", target_duration=4, extras={"generation_mode": "reference_video"}
    )
    project_path = pm.get_project_path("demo")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "video_units": [
                {
                    "unit_id": "E1U01",
                    "text": "镜头",
                    "duration_seconds": 4,
                    "generated_assets": {"video_clip": "reference_videos/E1U01.mp4"},
                }
            ],
        },
        "episode_1.json",
    )
    register_current_artifact(project_path, ArtifactKey.episode_script(1))
    staged = tmp_path / "video.mp4"
    make_test_video(staged, duration_sec=1)
    VersionManager(project_path).commit_staged_version(
        "reference_videos",
        "E1U01",
        "",
        staged_file=staged,
        current_file=project_path / "reference_videos/E1U01.mp4",
        source=MANUAL_UPLOAD_VERSION_SOURCE,
    )
    register_current_resource_artifact(
        project_path, resource_type="reference_videos", resource_id="E1U01", script_file="scripts/episode_1.json"
    )
    queue = GenerationQueue(session_factory=db_factory, project_manager=pm)
    planner = WorkflowPlanner(pm)
    request = WorkflowPlanRequest(episode_id=1)
    before = await planner.get_plan("demo", request, queue=queue)
    assert before.next_action.type == "create_edit_timeline"
    assert before.steps[-1].state == "ready"

    service = ProjectEventService(tmp_path, poll_interval=30)
    await service.start()
    try:
        async with service.stream_events("demo") as stream:
            assert (await anext(stream))[0] == "snapshot"
            with project_change_source("worker") if entry == "agent" else contextlib.nullcontext():
                if entry == "agent":
                    created = await run_declared_tool(
                        CREATE_TIMELINE,
                        ToolHarness("demo", tmp_path / "projects", pm, queue=queue),
                        {"from": "script", "episode": 1, "name": "完整版"},
                    )
                    assert created.problem is None
                else:
                    app = FastAPI()
                    override_auth(app)
                    app.dependency_overrides[require_project_migration_ok] = lambda: None
                    app.dependency_overrides[edit_timelines.get_edit_timeline_service] = lambda: EditTimelineService(pm)
                    app.dependency_overrides[edit_timelines.get_workflow_state_service] = lambda: WorkflowStateService(
                        pm
                    )
                    app.include_router(edit_timelines.router)
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post(
                            "/projects/demo/episodes/1/edit-timelines", json={"from": "script", "name": "完整版"}
                        )
                    assert response.status_code == 201
            event_name, payload = await _next_event(stream, timeout=1)
            assert event_name == "changes"
            assert payload["source"] == ("worker" if entry == "agent" else "webui")
            assert payload["changes"][0]["entity_type"] == "episode"
            assert payload["changes"][0]["episode"] == 1
            after = await planner.get_plan("demo", request, queue=queue)
            assert after.next_action.type == "none"
            assert workflow_finished(after.status)
            assert after.steps[-1].state == "completed"
    finally:
        await service.shutdown()


class TestProjectEventService:
    @pytest.mark.asyncio
    async def test_emitted_batch_is_broadcast_without_waiting_for_snapshot_diff(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=1.0)
        await service.start()

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            event_name, snapshot = await anext(stream)
            assert event_name == "snapshot"
            assert snapshot["fingerprint"]

            emit_project_change_batch(
                "demo",
                [
                    {
                        "entity_type": "segment",
                        "action": "storyboard_ready",
                        "entity_id": "E1S01",
                        "label": "分镜「E1S01」",
                        "focus": None,
                        "important": True,
                    }
                ],
                source="worker",
            )

            event_name, payload = await _next_event(stream, timeout=1.0)
            assert event_name == "changes"
            assert payload["source"] == "worker"
            assert payload["fingerprint"] == snapshot["fingerprint"]
            assert payload["changes"][0]["action"] == "storyboard_ready"

        await service.shutdown()

    async def test_assistant_session_resumed_is_broadcast_to_project_subscribers(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=1.0)
        await service.start()
        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                service.publish_assistant_session_resumed("demo", "sess-1")

                event_name, payload = await _next_event(stream, timeout=1.0)
                assert event_name == ASSISTANT_SESSION_RESUMED_EVENT
                assert payload == {"project_name": "demo", "session_id": "sess-1", "status": "running"}
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_stale_rebuild_does_not_broadcast_reverse_diff(self, tmp_path):
        """两次显式重建读盘耗时不同而乱序结算时，先读到旧盘的一方不广播反向变更。

        补扫对基线做 diff，故陈旧快照被写回基线会把实际存在的实体 diff 成 ``deleted``。
        重建锁把「读盘 → 结算基线 → 广播」串成一段读-改-写，后发起的一方等到前一方
        结算完才读盘，读到的必然更新。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()

        release_first = asyncio.Event()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                calls = 0
                first_read_done = asyncio.Event()
                second_read_done = asyncio.Event()
                loop = asyncio.get_running_loop()

                def _read_holding_first(read):
                    nonlocal calls
                    calls += 1
                    index = calls
                    result = read()
                    if index == 1:
                        loop.call_soon_threadsafe(first_read_done.set)
                        asyncio.run_coroutine_threadsafe(release_first.wait(), loop).result(timeout=5)
                    else:
                        loop.call_soon_threadsafe(second_read_done.set)
                    return result

                reader.hook = _read_holding_first

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")
                await asyncio.wait_for(first_read_done.wait(), timeout=5.0)

                project_json = pm.get_project_path("demo") / "project.json"
                data = json.loads(project_json.read_text(encoding="utf-8"))
                data.setdefault("characters", {})["新角色"] = {"name": "新角色", "description": "d"}
                project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

                # 第二次重建：无锁时它能在此期间读到新盘并抢先结算，等它的读盘完成信号，
                # 不用固定 sleep（调度慢于 sleep 时第二次重建还没开始，摘掉锁也测不出回归）。
                # 有锁时它被挡在锁外、信号必然等不到，这时超时本身就是锁生效的证据。
                emit_project_change_batch("demo", [_terminal_task_change("task-2")], source="worker")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(second_read_done.wait(), timeout=1.0)
                release_first.set()

                broadcast_changes = []
                for _ in range(2):
                    event_name, payload = await _next_event(stream, timeout=5.0)
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])
                while True:
                    try:
                        event_name, payload = await _next_event(stream, timeout=0.5)
                    except TimeoutError:
                        break
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])

                character_changes = [change for change in broadcast_changes if change["entity_type"] == "character"]
                assert [change["action"] for change in character_changes] == ["created"]
        finally:
            # 断言或 _next_event 提前失败时，被刻意挂起的重建线程仍卡在 release_first 上，
            # 放行后 shutdown 才收得掉 watch task 与 project_change_hints 的全局监听注册。
            release_first.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_emitted_batch_broadcasts_concurrent_change_swept_into_rebuild(self, tmp_path):
        """显式重建读盘捎带了不属于本批的文件变更时，该变更由紧随其后的补扫广播发出。

        显式重建把新快照写回基线。若某个变更落盘于读盘之前却不在本批 changes 里，
        它会被并入基线而从未广播——后续轮询扫描与基线已无差异，再没有任何机制
        为它补发。这里用「与该变更无关的终态事件」触发重建来覆盖这条路径。

        补扫走独立 payload：source 是 payload 级字段，捎带进来的变更来源与本批未必
        相同，并入本批会让前端的 webui 抑制判断落到错误的一侧。

        并发变更取「原地改写已索引的剧本文件」：这类变更不触发剧集索引同步，
        因而不会顺带发出 hint 唤醒轮询扫描，丢失是终局的。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "narration_text": "开场",
                    "image_prompt": "p",
                    "video_prompt": "v",
                    "characters_in_scene": [],
                    "scenes": [],
                    "props": [],
                    "generated_assets": _pending_assets(),
                }
            ],
        }
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        service = ProjectEventService(tmp_path, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                script["segments"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
                script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")

                event_name, payload = await _next_event(stream, timeout=2.0)
                assert event_name == "changes"
                assert [change["action"] for change in payload["changes"]] == ["task_succeeded"]
                assert payload["source"] == "worker"

                event_name, swept = await _next_event(stream, timeout=2.0)
                assert event_name == "changes"
                assert any(
                    change["action"] == "storyboard_ready" and change["entity_id"] == "E1S01"
                    for change in swept["changes"]
                )
                assert swept["source"] == "filesystem"
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_emitted_batch_does_not_duplicate_change_it_already_describes(self, tmp_path):
        """资产已落盘再发本批事件（worker 成功路径的真实次序）时，补扫不重复广播同一条变更。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "narration_text": "开场",
                    "image_prompt": "p",
                    "video_prompt": "v",
                    "characters_in_scene": [],
                    "scenes": [],
                    "props": [],
                    "generated_assets": _pending_assets(),
                }
            ],
        }
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        service = ProjectEventService(tmp_path, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                script["segments"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
                script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

                emit_project_change_batch(
                    "demo",
                    [
                        {
                            "entity_type": "segment",
                            "action": "storyboard_ready",
                            "entity_id": "E1S01",
                            "label": "分镜「E1S01」",
                            "focus": None,
                            "important": True,
                            "script_file": "episode_1.json",
                            "episode": 1,
                        }
                    ],
                    source="worker",
                )

                broadcast_changes = []
                event_name, payload = await _next_event(stream, timeout=2.0)
                assert event_name == "changes"
                broadcast_changes.extend(payload["changes"])
                while True:
                    try:
                        event_name, payload = await _next_event(stream, timeout=0.5)
                    except TimeoutError:
                        break
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])

                ready = [
                    change
                    for change in broadcast_changes
                    if change["action"] == "storyboard_ready" and change["entity_id"] == "E1S01"
                ]
                assert len(ready) == 1
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_reference_video_completion_is_broadcast_once(self, tmp_path):
        """参考生视频完成在发布方叫 ``reference_video_ready``、在快照差分里是 ``video_ready``，
        补扫按同一件事让给本批，只广播一次。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration", extras={"generation_mode": "reference_video"})

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "video_units": [
                {"unit_id": "E1U01", "duration_seconds": 8, "text": "开场", "generated_assets": _pending_assets()}
            ],
        }
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        service = ProjectEventService(tmp_path, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                script["video_units"][0]["generated_assets"]["video_clip"] = "videos/E1U01.mp4"
                script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
                emit_project_change_batch(
                    "demo",
                    [
                        {
                            "entity_type": "reference_unit",
                            "action": "reference_video_ready",
                            "entity_id": "E1U01",
                            "label": "视频单元「E1U01」",
                            "focus": None,
                            "important": True,
                        }
                    ],
                    source="worker",
                )

                broadcast_changes = []
                while True:
                    try:
                        event_name, payload = await _next_event(stream, timeout=0.5)
                    except TimeoutError:
                        break
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])

                assert [change["action"] for change in broadcast_changes if change["entity_id"] == "E1U01"] == [
                    "reference_video_ready"
                ]
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_project_manager_writes_outside_the_old_snapshot_fields_are_broadcast(self, tmp_path):
        """经项目管理器新增商品、改台词，订阅者都收到对应的项目事件。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "drama")
        scene = {
            "scene_id": "E1S01",
            "duration_seconds": 8,
            "utterances": [{"kind": "dialogue", "speaker": "甲", "text": "旧台词"}],
            "generated_assets": _pending_assets(),
        }
        with project_change_source("filesystem"):
            pm.save_script(
                "demo",
                {"episode": 1, "title": "第一集", "content_mode": "drama", "scenes": [scene]},
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                pm.upsert_assets("demo", "products", {"咖啡": {"description": "冷萃"}})
                with pm.locked_script("demo", "episode_1.json", validate=False) as script:
                    script["scenes"][0]["utterances"][0]["text"] = "新台词"

                expected = {("product", "created", "咖啡"), ("drama_scene", "updated", "E1S01")}
                seen: set[tuple] = set()
                while not expected <= seen:
                    event_name, payload = await _next_event(stream, timeout=2.0)
                    assert event_name == "changes"
                    seen |= {(c["entity_type"], c["action"], c["entity_id"]) for c in payload["changes"]}
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_data_root_kwarg_overrides_default_subdir(self, tmp_path):
        """显式传 data_root 时（生产入口传配置的数据根），事件流读的是该数据根下的项目。"""
        custom_projects = tmp_path / "external-data"
        pm = ProjectManager(custom_projects)
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, data_root=custom_projects, poll_interval=30.0)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, snapshot = await anext(stream)
                assert event_name == "snapshot"
                assert snapshot["fingerprint"]
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_sweep_leaves_change_described_by_another_inflight_batch(self, tmp_path):
        """另一批次的产物被本次重建读盘捎带时，补扫让给该批次广播，同一件事只发一次。

        两个生成任务几乎同时完成时，先取得重建锁的一方会读到对方已落盘的产物。补扫若把它
        一并广播，对方自己排队的批次仍会无条件再发一次，前端不跨批去重，用户看到两次完成。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "narration_text": "开场",
                    "image_prompt": "p",
                    "video_prompt": "v",
                    "characters_in_scene": [],
                    "scenes": [],
                    "props": [],
                    "generated_assets": _pending_assets(),
                }
            ],
        }
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()

        release_first = asyncio.Event()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                calls = 0
                first_read_done = asyncio.Event()
                loop = asyncio.get_running_loop()

                def _read_holding_first(read):
                    nonlocal calls
                    calls += 1
                    index = calls
                    if index == 1:
                        loop.call_soon_threadsafe(first_read_done.set)
                        asyncio.run_coroutine_threadsafe(release_first.wait(), loop).result(timeout=5)
                    return read()

                reader.hook = _read_holding_first

                emit_project_change_batch("demo", [_terminal_task_change("task-a")], source="worker")
                await asyncio.wait_for(first_read_done.wait(), timeout=5.0)

                script["segments"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
                script_path = pm.get_project_path("demo") / "scripts" / "episode_1.json"
                script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
                emit_project_change_batch(
                    "demo",
                    [
                        {
                            "entity_type": "segment",
                            "action": "storyboard_ready",
                            "entity_id": "E1S01",
                            "label": "分镜「E1S01」",
                            "focus": None,
                            "important": True,
                            "script_file": "episode_1.json",
                            "episode": 1,
                        }
                    ],
                    source="worker",
                )
                release_first.set()

                broadcast_changes = []
                while True:
                    try:
                        event_name, payload = await _next_event(stream, timeout=1.0)
                    except TimeoutError:
                        break
                    assert event_name == "changes"
                    broadcast_changes.extend(payload["changes"])

                ready = [
                    change
                    for change in broadcast_changes
                    if change["action"] == "storyboard_ready" and change["entity_id"] == "E1S01"
                ]
                assert len(ready) == 1
                assert ready[0]["focus"] is None
        finally:
            release_first.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_of_a_write_the_scan_read_missed_settles_the_next_scan(self, tmp_path):
        """多步 Web 写入的后一步落在扫描读盘之后、hint 在这轮结算前送达：下一轮扫描读到它，仍按 webui 广播。

        前一步的 hint 唤醒扫描；扫描读完盘、尚未结算时，后一步写盘并发出 hint。这轮扫描没读到后一步，
        它的来源标记要留给下一轮，不能随这轮结算一并清掉，否则下一轮读到的变更会被标成 filesystem。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()
        loop = asyncio.get_running_loop()
        read_done = asyncio.Event()
        release = threading.Event()

        def _read_then_hold(read):
            reader.hook = None
            state = read()
            loop.call_soon_threadsafe(read_done.set)
            assert release.wait(timeout=5)
            return state

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                assert (await anext(stream))[0] == "snapshot"
                reader.hook = _read_then_hold

                emit_project_change_hint("demo", source="webui")
                await asyncio.wait_for(read_done.wait(), timeout=5)
                # 写盘线程返回前已把 hint 投给事件循环，等它返回即保证 hint 先于这轮结算送达
                with project_change_source("webui"):
                    await asyncio.to_thread(pm.update_project, "demo", lambda project: project.update(style="水彩"))
                release.set()

                event_name, payload = await _next_event(stream, timeout=5)
                assert event_name == "changes"
                assert [change["entity_type"] for change in payload["changes"]] == ["project"]
                assert payload["source"] == "webui"
        finally:
            release.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_arriving_during_a_rebuild_read_settles_the_change_it_swept(self, tmp_path):
        """显式重建读盘期间落盘的写入被这次读盘读到、它的 hint 也在结算前送达：补扫按该 hint 的来源广播。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()
        loop = asyncio.get_running_loop()
        read_started = asyncio.Event()
        release = threading.Event()

        def _hold_then_read(read):
            reader.hook = None
            loop.call_soon_threadsafe(read_started.set)
            assert release.wait(timeout=5)
            return read()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                assert (await anext(stream))[0] == "snapshot"
                reader.hook = _hold_then_read

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")
                await asyncio.wait_for(read_started.wait(), timeout=5)
                with project_change_source("webui"):
                    await asyncio.to_thread(pm.update_project, "demo", lambda project: project.update(style="水彩"))
                release.set()

                event_name, own = await _next_event(stream, timeout=5)
                assert (event_name, own["source"]) == ("changes", "worker")
                assert [change["entity_type"] for change in own["changes"]] == ["task"]
                event_name, swept = await _next_event(stream, timeout=5)
                assert event_name == "changes"
                assert [change["entity_type"] for change in swept["changes"]] == ["project"]
                assert swept["source"] == "webui"
        finally:
            release.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_source_repeated_during_rebuild_read_is_not_dropped(self, tmp_path):
        """重建读盘期间重复到达的来源标记不被写回时清掉，下一轮扫描仍据它解析来源。

        读盘前在册的标记整体换出而非事后减去：同一来源在窗口内再次 hint 时，集合语义下
        「减去旧集合」会把新标记一并抹掉，那条变更会在下一轮扫描被误标成 filesystem，
        绕过前端对 WebUI 自身编辑的通知抑制。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=30.0, read_state=reader)
        await service.start()

        release_read = asyncio.Event()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                channel = service._channels["demo"]
                assert channel.pending_sources == set()
                channel.pending_sources.add("webui")

                read_started = asyncio.Event()
                loop = asyncio.get_running_loop()

                def _read_waiting(read):
                    loop.call_soon_threadsafe(read_started.set)
                    asyncio.run_coroutine_threadsafe(release_read.wait(), loop).result(timeout=5)
                    return read()

                reader.hook = _read_waiting

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")
                await asyncio.wait_for(read_started.wait(), timeout=5.0)

                channel.pending_sources.add("webui")
                release_read.set()

                event_name, _payload = await _next_event(stream, timeout=5.0)
                assert event_name == "changes"

                assert channel.pending_sources == {"webui"}
                assert service._resolve_batch_source(channel.pending_sources) == "webui"
        finally:
            release_read.set()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_change_landing_after_rebuild_read_is_still_broadcast(self, tmp_path):
        """变更落盘于「重建读盘完成之后、状态写回之前」时，仍能广播到订阅者。

        注入点是读盘 seam：真实读盘返回后再落盘，精确构造该时序窗口。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        reader = _ControlledReader(pm)
        service = ProjectEventService(tmp_path, poll_interval=0.05, read_state=reader)
        await service.start()

        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                armed = False

                def _read_then_land(read):
                    nonlocal armed
                    result = read()
                    if armed:
                        armed = False
                        project_json = pm.get_project_path("demo") / "project.json"
                        data = json.loads(project_json.read_text(encoding="utf-8"))
                        data.setdefault("characters", {})["新角色"] = {"description": "d"}
                        project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    return result

                reader.hook = _read_then_land
                armed = True

                emit_project_change_batch("demo", [_terminal_task_change("task-1")], source="worker")

                deadline = 3.0
                for _ in range(10):
                    event_name, payload = await _next_event(stream, timeout=deadline)
                    assert event_name == "changes"
                    if any(
                        (change["entity_type"], change["action"], change["entity_id"])
                        == ("character", "created", "新角色")
                        for change in payload["changes"]
                    ):
                        break
                else:
                    raise AssertionError("窗口内落盘的角色变更从未广播")
        finally:
            await service.shutdown()

    @pytest.mark.parametrize("edited_input", ["project_json", "existing_script", "new_script"])
    async def test_polling_pushes_change_to_any_input_file(self, tmp_path, edited_input):
        """首轮快照之后，任一输入文件在盘上变化（绕过 hint，只靠轮询比对输入标识发现）：照常推送。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "drama")
        scene = {
            "scene_id": "E1S01",
            "duration_seconds": 8,
            "utterances": [{"kind": "dialogue", "speaker": "甲", "text": "旧台词"}],
            "generated_assets": _pending_assets(),
        }
        script = {"episode": 1, "title": "第一集", "content_mode": "drama", "scenes": [scene]}
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)
        project_path = pm.get_project_path("demo")

        def edit_on_disk() -> None:
            if edited_input == "project_json":
                path = project_path / ProjectManager.PROJECT_FILE
                data = json.loads(path.read_text(encoding="utf-8"))
                data.setdefault("characters", {})["新角色"] = {"description": "d"}
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            elif edited_input == "existing_script":
                path = project_path / "scripts" / "episode_1.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                data["scenes"][0]["utterances"][0]["text"] = "新台词，比旧台词长"
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            else:
                other = {**script, "episode": 2, "title": "第二集"}
                (project_path / "scripts" / "episode_2.json").write_text(
                    json.dumps(other, ensure_ascii=False), encoding="utf-8"
                )

        expected_entity_type = {
            "project_json": "character",
            "existing_script": "drama_scene",
            "new_script": "episode",
        }[edited_input]

        service = ProjectEventService(tmp_path, poll_interval=0.1)
        await service.start()
        try:
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                event_name, _snapshot = await anext(stream)
                assert event_name == "snapshot"

                await asyncio.to_thread(edit_on_disk)

                seen: set[str] = set()
                while expected_entity_type not in seen:
                    event_name, payload = await _next_event(stream, timeout=3.0)
                    assert event_name == "changes"
                    seen |= {change["entity_type"] for change in payload["changes"]}
        finally:
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_subscribe_cancellation_cleans_up_subscriber(self, tmp_path, monkeypatch):
        """客户端在首次扫描期间断开 → _subscribe 被取消 → 订阅者与 watch task 不泄漏。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        watch_started = asyncio.Event()

        async def _never_ready(project_name, channel):
            watch_started.set()
            await asyncio.Future()

        monkeypatch.setattr(service, "_watch_project", _never_ready)

        async def subscribe():
            async with service.stream_events("demo"):
                pytest.fail("首次扫描完成前，订阅不应返回")

        task = asyncio.create_task(subscribe())
        try:
            await asyncio.wait_for(watch_started.wait(), timeout=5)
            channel = service._channels["demo"]
            assert channel.sse.has_subscribers
            watch_task = channel.task
            assert watch_task is not None

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert "demo" not in service._channels
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(watch_task, timeout=5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_new_subscriber_entering_during_stop_watch_stays_registered(self, tmp_path, monkeypatch):
        """末订阅者收尾挂起在 watch task 收尾等待点时，并发进入的新订阅者归属注册表现行通道，
        收得到经注册表路由的 hint 广播，且旧 watch task 退役、无脱离注册表的 task。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=1.0)
        await service.start()

        entered_cancel = asyncio.Event()
        allow_exit = asyncio.Event()
        stop_task: asyncio.Task | None = None

        try:

            async def _controlled_watch(project_name, channel):
                channel.ready_event.set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    entered_cancel.set()
                    await allow_exit.wait()
                    raise

            monkeypatch.setattr(service, "_watch_project", _controlled_watch)

            _sse_a, queue_a, _snap_a = await service._subscribe("demo")
            old_channel = service._channels["demo"]
            old_watch_task = old_channel.task

            stop_task = asyncio.create_task(service._unsubscribe("demo", queue_a))
            await asyncio.wait_for(entered_cancel.wait(), timeout=1.0)

            _sse_b, queue_b, _snap_b = await service._subscribe("demo")

            allow_exit.set()
            await asyncio.wait_for(stop_task, timeout=1.0)

            assert "demo" in service._channels
            assert service._channels["demo"] is not old_channel

            service._apply_emitted_batch(
                "demo",
                "worker",
                (
                    {
                        "entity_type": "segment",
                        "action": "storyboard_ready",
                        "entity_id": "E1S01",
                        "label": "分镜「E1S01」",
                        "focus": None,
                        "important": True,
                    },
                ),
            )
            await asyncio.gather(*list(service._pending_batch_tasks), return_exceptions=True)
            event_name, _payload = queue_b.get_nowait()
            assert event_name == "changes"

            assert old_watch_task.done()
        finally:
            # 断言或 wait_for 提前失败时，被刻意挂起的 watch/stop task 仍需释放，
            # 否则会跨用例泄漏后台任务与 project_change_hints 的全局监听注册。
            allow_exit.set()
            if stop_task is not None and not stop_task.done():
                await asyncio.gather(stop_task, return_exceptions=True)
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_watch_terminates_stream_when_project_directory_deleted(self, tmp_path, caplog):
        """订阅存续期间删除项目目录：一个轮询周期内扫描终止——广播终止事件、
        流正常结束、通道从注册表移除，仅记一条 INFO 日志，无 ERROR/traceback。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        with caplog.at_level(logging.INFO, logger="server.services.project.project_events"):
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                first = await anext(stream)
                assert first[0] == "snapshot"

                pm.delete_project_directory("demo")

                event_name, payload = await _next_event(stream, timeout=1.5)
                assert event_name == PROJECT_DELETED_EVENT
                assert payload == {"project_name": "demo"}

                with pytest.raises(StopAsyncIteration):
                    await anext(stream)

        assert "demo" not in service._channels
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)
        info_records = [
            record for record in caplog.records if record.levelno == logging.INFO and "已被删除" in record.message
        ]
        assert len(info_records) == 1

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_watch_keeps_error_logging_when_only_project_json_missing(self, tmp_path, caplog):
        """项目目录仍存在、仅 project.json 缺失——不属于本次修复范围：维持现状，
        按通用异常兜底记 ERROR，通道不终止、继续轮询重试。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        with caplog.at_level(logging.INFO, logger="server.services.project.project_events"):
            async with service.stream_events("demo", idle_timeout=0.1):
                (pm.get_project_path("demo") / ProjectManager.PROJECT_FILE).unlink()
                await asyncio.sleep(0.3)
                assert "demo" in service._channels

        assert any(record.levelno >= logging.ERROR for record in caplog.records)
        assert not any("已被删除" in record.message for record in caplog.records)

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_rebuild_terminates_channel_without_error_when_project_deleted(self, tmp_path, caplog):
        """hint 触发的显式重建路径对已删除项目同样走终止处理，不产生 ERROR 日志。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=5.0)
        await service.start()

        with caplog.at_level(logging.INFO, logger="server.services.project.project_events"):
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                first = await anext(stream)
                assert first[0] == "snapshot"

                pm.delete_project_directory("demo")

                emit_project_change_batch(
                    "demo",
                    [
                        {
                            "entity_type": "segment",
                            "action": "updated",
                            "entity_id": "E1S01",
                            "label": "分镜「E1S01」",
                            "focus": None,
                            "important": False,
                        }
                    ],
                    source="worker",
                )

                event_name, payload = await _next_event(stream, timeout=1.5)
                assert event_name == PROJECT_DELETED_EVENT
                assert payload == {"project_name": "demo"}

        assert "demo" not in service._channels
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_new_subscriber_after_project_recreated_gets_fresh_channel(self, tmp_path):
        """项目删除后原通道终止；同名项目重建后新订阅走全新通道，行为与现在一致。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            first = await anext(stream)
            assert first[0] == "snapshot"

            pm.delete_project_directory("demo")

            event_name, _payload = await _next_event(stream, timeout=1.5)
            assert event_name == PROJECT_DELETED_EVENT

        assert "demo" not in service._channels

        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo Reborn", "Anime", "narration")

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            first = await anext(stream)
            assert first[0] == "snapshot"
            assert "demo" in service._channels

        await service.shutdown()


async def test_shutdown_ends_first_snapshot_wait_and_late_project_stream(tmp_path):
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    reading = threading.Event()
    release = threading.Event()

    def read(project):
        reading.set()
        release.wait(5)
        return read_project_state(pm, project)

    service = ProjectEventService(tmp_path, read_state=read)

    async def collect():
        async with service.stream_events("demo") as stream:
            return [item async for item in stream]

    async with asyncio.timeout(3):
        pending = asyncio.create_task(collect())
        try:
            assert await asyncio.to_thread(reading.wait, 2)
            service.stop_streams()
            assert await pending == []
            assert await collect() == []
        finally:
            release.set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await service.shutdown()
