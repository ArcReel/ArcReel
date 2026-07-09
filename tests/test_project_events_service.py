import asyncio
import json
import logging

import pytest

from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from lib.script_skeleton import SKELETONS
from server.services.project_events import (
    _SKELETON_ANCHOR_TYPES,
    _SKELETON_ENTITY_TYPES,
    _SKELETON_ITEM_NOUNS,
    PROJECT_DELETED_EVENT,
    ProjectEventService,
)


def _pending_assets() -> dict:
    return {
        "storyboard_image": None,
        "video_clip": None,
        "video_uri": None,
        "status": "pending",
    }


async def _next_event(stream, *, timeout: float) -> tuple[str, dict]:
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


class TestProjectEventService:
    def test_diff_snapshots_reports_character_and_storyboard_changes(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        with project_change_source("filesystem"):
            pm.save_script(
                "demo",
                {
                    "episode": 1,
                    "title": "第一集",
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": "E1S01",
                            "duration_seconds": 4,
                            "segment_break": False,
                            "characters_in_segment": [],
                            "scenes": [],
                            "props": [],
                            "image_prompt": "old",
                            "video_prompt": "old",
                            "generated_assets": {
                                "storyboard_image": None,
                                "video_clip": None,
                                "video_uri": None,
                                "status": "pending",
                            },
                        }
                    ],
                },
                "episode_1.json",
                validate=False,  # 事件 diff 测试用简化替身剧本
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("demo")

        project = pm.load_project("demo")
        project["characters"]["Hero"] = {
            "description": "主角",
            "voice_style": "冷静",
            "character_sheet": "",
            "reference_image": "",
        }
        with project_change_source("filesystem"):
            pm.save_project("demo", project)

        script = pm.load_script("demo", "episode_1.json")
        segment = script["segments"][0]
        segment["image_prompt"] = "new"
        segment["generated_assets"]["storyboard_image"] = "storyboards/scene_E1S01.png"
        segment["generated_assets"]["status"] = "storyboard_ready"
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        current = service._build_snapshot("demo")
        changes = service._diff_snapshots(previous, current)

        assert any(change["entity_type"] == "character" and change["action"] == "created" for change in changes)
        assert any(change["action"] == "storyboard_ready" for change in changes)
        segment_updated = [c for c in changes if c["entity_type"] == "segment" and c["action"] == "updated"]
        assert segment_updated
        # narration 分镜走时间线画布：锚点类型恒为 segment（回归守卫，不得漂移）。
        assert all(c["focus"]["anchor_type"] == "segment" for c in segment_updated)

    def test_build_snapshot_survives_null_episodes(self, tmp_path):
        # project.json 的 episodes 显式为 null 时快照构建不崩:load_project 直接回读磁盘
        # JSON、不规范化 episodes,读侧按 fail-soft 用 ``or []`` 兜底而非 ``get(..., [])``。
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        project = pm.load_project("demo")
        project["episodes"] = None
        project_file = pm.get_project_path("demo") / ProjectManager.PROJECT_FILE
        project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

        service = ProjectEventService(tmp_path)
        snapshot = service._build_snapshot("demo")

        assert snapshot["project"]["episodes"] == {}

    def test_diff_snapshots_reports_project_metadata_and_new_segments(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        with project_change_source("filesystem"):
            pm.save_script(
                "demo",
                {
                    "episode": 1,
                    "title": "第一集",
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": "E1S01",
                            "duration_seconds": 4,
                            "segment_break": False,
                            "characters_in_segment": [],
                            "scenes": [],
                            "props": [],
                            "image_prompt": "old",
                            "video_prompt": "old",
                            "generated_assets": {
                                "storyboard_image": None,
                                "video_clip": None,
                                "video_uri": None,
                                "status": "pending",
                            },
                        }
                    ],
                },
                "episode_1.json",
                validate=False,  # 事件 diff 测试用简化替身剧本
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("demo")

        project = pm.load_project("demo")
        project["title"] = "Demo Updated"
        project["style_description"] = "moody lighting"
        with project_change_source("filesystem"):
            pm.save_project("demo", project)

        script = pm.load_script("demo", "episode_1.json")
        script["segments"].append(
            {
                "segment_id": "E1S02",
                "duration_seconds": 4,
                "segment_break": False,
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "image_prompt": "new",
                "video_prompt": "new",
                "generated_assets": {
                    "storyboard_image": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            }
        )
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        current = service._build_snapshot("demo")
        changes = service._diff_snapshots(previous, current)

        assert any(change["entity_type"] == "project" and change["action"] == "updated" for change in changes)
        assert any(
            change["entity_type"] == "segment" and change["action"] == "created" and change["entity_id"] == "E1S02"
            for change in changes
        )

    @pytest.mark.asyncio
    async def test_poll_detects_direct_script_write_and_syncs_episode_index(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            # 首个事件是 snapshot 元组。
            first = await anext(stream)
            assert first[0] == "snapshot"
            assert first[1]["project_name"] == "demo"

            script_path = pm.get_project_path("demo") / "scripts" / "episode_2.json"
            script_path.write_text(
                json.dumps(
                    {
                        "episode": 2,
                        "title": "第二集",
                        "content_mode": "narration",
                        "segments": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            event_name, payload = await _next_event(stream, timeout=1.5)
            assert event_name == "changes"
            assert payload["source"] == "filesystem"
            assert any(
                change["entity_type"] == "episode" and change["action"] == "created" and change["episode"] == 2
                for change in payload["changes"]
            )
            assert any(episode["episode"] == 2 for episode in pm.load_project("demo")["episodes"])

        await service.shutdown()

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

    @pytest.mark.asyncio
    async def test_subscribe_cancellation_cleans_up_subscriber(self, tmp_path, monkeypatch):
        """客户端在首次扫描期间断开 → _subscribe 被取消 → 订阅者与 watch task 不泄漏。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        # 模拟首次扫描卡住:watch task 永不 set ready_event,_subscribe 会 park 在 wait()。
        async def _never_ready(project_name, channel):
            await asyncio.sleep(3600)

        monkeypatch.setattr(service, "_watch_project", _never_ready)

        task = asyncio.create_task(service._subscribe("demo"))
        await asyncio.sleep(0.05)  # 让 _subscribe 注册 queue 并 park
        channel = service._channels["demo"]
        assert channel.sse.has_subscribers  # 已注册
        watch_task = channel.task

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # 取消后:订阅者被清理、channel 被弹出、watch task 被取消(不泄漏)。
        assert "demo" not in service._channels
        await asyncio.sleep(0)  # 让 watch task 的取消落定
        assert watch_task.cancelled() or watch_task.done()

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
                # 立即放行 _subscribe 的 ready 等待；被取消时停在收尾点，撑开竞态窗口。
                channel.ready_event.set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    entered_cancel.set()
                    await allow_exit.wait()
                    raise

            monkeypatch.setattr(service, "_watch_project", _controlled_watch)

            # 订阅者 A 拉起旧通道与旧 watch task。
            _sse_a, queue_a, _snap_a = await service._subscribe("demo")
            old_channel = service._channels["demo"]
            old_watch_task = old_channel.task

            # A 退订触发末订阅者钩子；钩子停在等已取消 watch task 退出的收尾点。
            stop_task = asyncio.create_task(service._unsubscribe("demo", queue_a))
            await asyncio.wait_for(entered_cancel.wait(), timeout=1.0)

            # 窗口内新订阅者 B 进入。
            _sse_b, queue_b, _snap_b = await service._subscribe("demo")

            # 放行旧 watch task，让末订阅者钩子跑完收尾。
            allow_exit.set()
            await asyncio.wait_for(stop_task, timeout=1.0)

            # B 归属注册表中的现行通道，旧通道已退役。
            assert "demo" in service._channels
            assert service._channels["demo"] is not old_channel

            # 经注册表路由的 hint 广播抵达 B。
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

            # 旧 watch task 退役，未脱离注册表继续运行。
            assert old_watch_task.done()
        finally:
            # 断言或 wait_for 提前失败时，被刻意挂起的 watch/stop task 仍需释放，
            # 否则会跨用例泄漏后台任务与 project_change_hints 的全局监听注册。
            allow_exit.set()
            if stop_task is not None and not stop_task.done():
                await asyncio.gather(stop_task, return_exceptions=True)
            await service.shutdown()

    def test_projects_root_kwarg_overrides_default_subdir(self, tmp_path):
        """显式传 projects_root 时，service.pm 走该目录而非 project_root/'projects'。

        覆盖 ARCREEL_DATA_DIR 场景：app.py 启动时传 ``app_data_dir()`` 进来，
        事件监听应跟着切换，不能继续指向旧的 ``project_root/projects``。
        """
        custom_projects = tmp_path / "external-data"
        pm = ProjectManager(custom_projects)
        pm.create_project("demo")

        service = ProjectEventService(tmp_path, projects_root=custom_projects)

        assert service.pm.projects_root == custom_projects.resolve()
        assert service.pm.get_project_path("demo") == (custom_projects / "demo").resolve()

    def test_diff_snapshots_reports_ad_shot_lifecycle_events(self, tmp_path):
        """ad(shots) 项目的分镜级事件：created / storyboard_ready / video_ready / updated。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("ad-demo")
        pm.create_project_metadata("ad-demo", "Ad", "Anime", "ad")

        with project_change_source("filesystem"):
            pm.save_script(
                "ad-demo",
                {
                    "episode": 1,
                    "title": "广告",
                    "content_mode": "ad",
                    "shots": [
                        {
                            "shot_id": "E1S01",
                            "duration_seconds": 4,
                            "characters_in_shot": ["Hero"],
                            "scenes": [],
                            "props": [],
                            "image_prompt": "old",
                            "video_prompt": "old",
                            "generated_assets": _pending_assets(),
                        }
                    ],
                },
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("ad-demo")
        assert previous["scripts"]["episode_1.json"]["kind"] == "shots"
        assert previous["scripts"]["episode_1.json"]["items"]["E1S01"]["characters"] == ["Hero"]

        script = pm.load_script("ad-demo", "episode_1.json")
        script["shots"][0]["image_prompt"] = "new"
        script["shots"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
        script["shots"].append(
            {
                "shot_id": "E1S02",
                "duration_seconds": 4,
                "characters_in_shot": [],
                "scenes": [],
                "props": [],
                "image_prompt": "p",
                "video_prompt": "v",
                "generated_assets": _pending_assets(),
            }
        )
        with project_change_source("filesystem"):
            pm.save_script("ad-demo", script, "episode_1.json", validate=False)

        mid = service._build_snapshot("ad-demo")
        changes = service._diff_snapshots(previous, mid)
        assert any(c["action"] == "created" and c["entity_id"] == "E1S02" for c in changes)
        assert any(c["action"] == "storyboard_ready" and c["entity_id"] == "E1S01" for c in changes)
        assert any(c["action"] == "updated" and c["entity_id"] == "E1S01" for c in changes)
        shot_changes = [c for c in changes if c["entity_type"] == "shot"]
        assert shot_changes and all(c["label"].startswith("镜头") for c in shot_changes)
        # ad 镜头走时间线画布：可导航事件的锚点类型为 segment（ShotSplitView 守卫）。
        assert all(c["focus"]["anchor_type"] == "segment" for c in shot_changes if c["focus"] is not None)

        script = pm.load_script("ad-demo", "episode_1.json")
        script["shots"][0]["generated_assets"]["video_clip"] = "videos/E1S01.mp4"
        with project_change_source("filesystem"):
            pm.save_script("ad-demo", script, "episode_1.json", validate=False)
        final = service._build_snapshot("ad-demo")
        video_changes = service._diff_snapshots(mid, final)
        assert any(c["action"] == "video_ready" and c["entity_id"] == "E1S01" for c in video_changes)

    def test_diff_snapshots_reports_drama_scene_lifecycle_events(self, tmp_path):
        """drama(scenes) 项目的分镜级事件：created / storyboard_ready / video_ready。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("drama-demo")
        pm.create_project_metadata("drama-demo", "Drama", "Anime", "drama")

        with project_change_source("filesystem"):
            pm.save_script(
                "drama-demo",
                {
                    "episode": 1,
                    "title": "剧集",
                    "content_mode": "drama",
                    "scenes": [
                        {
                            "scene_id": "E1S01",
                            "duration_seconds": 8,
                            "characters_in_scene": ["Hero"],
                            "scenes": [],
                            "props": [],
                            "image_prompt": "old",
                            "video_prompt": "old",
                            "generated_assets": _pending_assets(),
                        }
                    ],
                },
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("drama-demo")
        assert previous["scripts"]["episode_1.json"]["kind"] == "scenes"
        assert previous["scripts"]["episode_1.json"]["items"]["E1S01"]["characters"] == ["Hero"]

        script = pm.load_script("drama-demo", "episode_1.json")
        script["scenes"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
        script["scenes"][0]["generated_assets"]["video_clip"] = "videos/E1S01.mp4"
        script["scenes"].append(
            {
                "scene_id": "E1S02",
                "duration_seconds": 8,
                "characters_in_scene": [],
                "scenes": [],
                "props": [],
                "image_prompt": "p",
                "video_prompt": "v",
                "generated_assets": _pending_assets(),
            }
        )
        with project_change_source("filesystem"):
            pm.save_script("drama-demo", script, "episode_1.json", validate=False)

        current = service._build_snapshot("drama-demo")
        changes = service._diff_snapshots(previous, current)
        assert any(c["action"] == "created" and c["entity_id"] == "E1S02" for c in changes)
        assert any(c["action"] == "storyboard_ready" and c["entity_id"] == "E1S01" for c in changes)
        assert any(c["action"] == "video_ready" and c["entity_id"] == "E1S01" for c in changes)
        scene_changes = [c for c in changes if c["entity_type"] == "drama_scene"]
        assert scene_changes and all(c["label"].startswith("场景") for c in scene_changes)
        # drama 场景走时间线画布：可导航事件的锚点类型为 segment。
        assert all(c["focus"]["anchor_type"] == "segment" for c in scene_changes if c["focus"] is not None)

    def test_diff_snapshots_reports_reference_video_unit_lifecycle_events(self, tmp_path):
        """reference_video(video_units) 项目的分镜级事件全周期，且 characters 从 references 派生。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("ref-demo")
        pm.create_project_metadata("ref-demo", "Ref", "Anime", "narration")

        with project_change_source("filesystem"):
            pm.save_script(
                "ref-demo",
                {
                    "episode": 1,
                    "title": "参考",
                    "content_mode": "narration",
                    "generation_mode": "reference_video",
                    "video_units": [
                        {
                            "unit_id": "E1U01",
                            "duration_seconds": 8,
                            "shots": [{"duration": 4, "text": "@[Hero] 登场"}],
                            "references": [
                                {"type": "character", "name": "Hero"},
                                {"type": "scene", "name": "街道"},
                            ],
                            "generated_assets": _pending_assets(),
                        }
                    ],
                },
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("ref-demo")
        prev_meta = previous["scripts"]["episode_1.json"]
        assert prev_meta["kind"] == "video_units"
        # characters 只取 references 中 type==character 的条目，不含 scene。
        assert prev_meta["items"]["E1U01"]["characters"] == ["Hero"]

        script = pm.load_script("ref-demo", "episode_1.json")
        script["video_units"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1U01.png"
        script["video_units"][0]["references"].append({"type": "character", "name": "Villain"})
        script["video_units"].append(
            {
                "unit_id": "E1U02",
                "duration_seconds": 6,
                "shots": [{"duration": 6, "text": "空镜"}],
                "references": [],
                "generated_assets": _pending_assets(),
            }
        )
        with project_change_source("filesystem"):
            pm.save_script("ref-demo", script, "episode_1.json", validate=False)

        mid = service._build_snapshot("ref-demo")
        # 新增的 reference 角色反映进 characters。
        assert mid["scripts"]["episode_1.json"]["items"]["E1U01"]["characters"] == ["Hero", "Villain"]
        changes = service._diff_snapshots(previous, mid)
        assert any(c["action"] == "created" and c["entity_id"] == "E1U02" for c in changes)
        assert any(c["action"] == "storyboard_ready" and c["entity_id"] == "E1U01" for c in changes)
        assert any(c["action"] == "updated" and c["entity_id"] == "E1U01" for c in changes)
        unit_changes = [c for c in changes if c["entity_type"] == "reference_unit"]
        assert unit_changes and all(c["label"].startswith("视频单元") for c in unit_changes)
        # 参考生视频单元走参考画布：可导航事件（created/updated）的锚点类型为 reference_unit，
        # 前端据此切到 units tab 并选中对应单元——本 issue 的核心修复。
        navigable = [c for c in unit_changes if c["action"] in ("created", "updated")]
        assert navigable and all(c["focus"]["anchor_type"] == "reference_unit" for c in navigable)

        script = pm.load_script("ref-demo", "episode_1.json")
        script["video_units"][0]["generated_assets"]["video_clip"] = "videos/E1U01.mp4"
        with project_change_source("filesystem"):
            pm.save_script("ref-demo", script, "episode_1.json", validate=False)
        final = service._build_snapshot("ref-demo")
        video_changes = service._diff_snapshots(mid, final)
        assert any(c["action"] == "video_ready" and c["entity_id"] == "E1U01" for c in video_changes)

    def test_diff_snapshots_reports_reference_video_content_edits(self, tmp_path):
        """reference_video 单元的内容体编辑（成员镜头文本 / 场景引用）触发 updated 事件——

        角色引用之外的内容改动此前不发 updated：快照只捕获 characters 与 duration，未纳成员镜头
        文本与非角色引用，单元内容真实变更却在差分里恒等。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("ref-edit")
        pm.create_project_metadata("ref-edit", "Ref", "Anime", "narration")

        with project_change_source("filesystem"):
            pm.save_script(
                "ref-edit",
                {
                    "episode": 1,
                    "title": "参考",
                    "content_mode": "narration",
                    "generation_mode": "reference_video",
                    "video_units": [
                        {
                            "unit_id": "E1U01",
                            "duration_seconds": 8,
                            "shots": [{"duration": 4, "text": "@[Hero] 登场"}],
                            "references": [{"type": "character", "name": "Hero"}],
                            "generated_assets": _pending_assets(),
                        }
                    ],
                },
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("ref-edit")

        # 仅改成员镜头文本，不动角色 / 时长 / 资产。
        script = pm.load_script("ref-edit", "episode_1.json")
        script["video_units"][0]["shots"][0]["text"] = "@[Hero] 转身离去"
        with project_change_source("filesystem"):
            pm.save_script("ref-edit", script, "episode_1.json", validate=False)
        after_text = service._build_snapshot("ref-edit")
        assert after_text["scripts"]["episode_1.json"]["items"]["E1U01"]["shots"] == [
            {"text": "@[Hero] 转身离去", "duration": 4}
        ]
        text_changes = service._diff_snapshots(previous, after_text)
        assert any(c["action"] == "updated" and c["entity_id"] == "E1U01" for c in text_changes)

        # 追加场景引用（非角色）：触发 updated 且派生进 scenes（不误入 characters）。
        script = pm.load_script("ref-edit", "episode_1.json")
        script["video_units"][0]["references"].append({"type": "scene", "name": "码头"})
        with project_change_source("filesystem"):
            pm.save_script("ref-edit", script, "episode_1.json", validate=False)
        after_scene = service._build_snapshot("ref-edit")
        scene_item = after_scene["scripts"]["episode_1.json"]["items"]["E1U01"]
        assert scene_item["scenes"] == ["码头"]
        assert scene_item["characters"] == ["Hero"]
        scene_changes = service._diff_snapshots(after_text, after_scene)
        assert any(c["action"] == "updated" and c["entity_id"] == "E1U01" for c in scene_changes)

    @pytest.mark.parametrize("kind", sorted(SKELETONS))
    def test_normalize_snapshot_covers_every_skeleton_kind(self, tmp_path, kind):
        """每个骨架种类都被 _normalize_script_snapshot 正确抽取条目——

        新增第五种骨架而未在归一化里处置时，本参数化断言会为该 kind 失败，
        而非复刻 ad/reference_video 被静默跳过的路径。
        """
        content_mode = {
            "segments": "narration",
            "scenes": "drama",
            "shots": "ad",
            "video_units": "narration",
        }[kind]
        skeleton = SKELETONS[kind]
        item: dict = {skeleton.id_field: "X1"}
        if skeleton.chars_field is not None:
            item[skeleton.chars_field] = ["Hero"]
        else:
            item["references"] = [{"type": "character", "name": "Hero"}]
        script = {"content_mode": content_mode, kind: [item]}

        service = ProjectEventService(tmp_path)
        normalized = service._normalize_script_snapshot(script)
        assert normalized["kind"] == kind
        assert "X1" in normalized["items"]
        assert normalized["items"]["X1"]["characters"] == ["Hero"]
        label = service._build_script_item_label("X1", normalized)
        assert label.endswith("「X1」") and not label.startswith("「")

    def test_every_skeleton_kind_has_label_noun(self):
        """标签名词表覆盖全部骨架种类——第五种骨架出现时此处失败，逼出名词补全。"""
        assert set(_SKELETON_ITEM_NOUNS) == set(SKELETONS)

    def test_every_skeleton_kind_has_entity_and_anchor_type(self):
        """实体/锚点类型表覆盖全部骨架种类——第五种骨架出现时此处失败，逼出补全。"""
        assert set(_SKELETON_ENTITY_TYPES) == set(SKELETONS)
        assert set(_SKELETON_ANCHOR_TYPES) == set(SKELETONS)

    @pytest.mark.parametrize(
        ("kind", "content_mode", "generation_mode", "entity_type", "anchor_type"),
        [
            ("segments", "narration", None, "segment", "segment"),
            ("scenes", "drama", None, "drama_scene", "segment"),
            ("shots", "ad", None, "shot", "segment"),
            ("video_units", "narration", "reference_video", "reference_unit", "reference_unit"),
        ],
    )
    def test_script_item_change_carries_kind_specific_types(
        self, tmp_path, kind, content_mode, generation_mode, entity_type, anchor_type
    ):
        """分镜级事件的 entity_type（分组标签）与 focus.anchor_type（画布滚动目标）按骨架种类推导。"""
        skeleton = SKELETONS[kind]
        item: dict = {skeleton.id_field: "X1"}
        if skeleton.chars_field is not None:
            item[skeleton.chars_field] = ["Hero"]
        else:
            item["references"] = [{"type": "character", "name": "Hero"}]
        script: dict = {"episode": 1, "content_mode": content_mode, kind: [item]}
        if generation_mode is not None:
            script["generation_mode"] = generation_mode

        service = ProjectEventService(tmp_path)
        meta = service._normalize_script_snapshot(script)
        assert meta["kind"] == kind

        change = service._build_script_item_change(
            action="created",
            item_id="X1",
            script_file="episode_1.json",
            script_meta=meta,
            important=True,
        )
        assert change["entity_type"] == entity_type
        assert change["focus"]["anchor_type"] == anchor_type
        assert change["focus"]["anchor_id"] == "X1"

    def test_diff_snapshots_reports_ad_reference_unit_video_ready(self, tmp_path):
        """ad + reference_video：unit 的 video_clip 空→非空发一条 video_ready（实体类型 reference_unit）。

        成片写在派生索引 reference_units 各 unit 的 generated_assets，内容骨架 shots 不承载
        该路径产物；组合按项目声明的 generation_mode 分派（effective_mode），不嗅探剧本形状。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("ad-ref")
        pm.create_project_metadata("ad-ref", "AdRef", "Anime", "ad", extras={"generation_mode": "reference_video"})

        with project_change_source("filesystem"):
            pm.save_script(
                "ad-ref",
                {
                    "episode": 1,
                    "title": "广告",
                    "content_mode": "ad",
                    "shots": [
                        {
                            "shot_id": "E1S01",
                            "duration_seconds": 4,
                            "characters_in_shot": [],
                            "scenes": [],
                            "props": [],
                            "image_prompt": "p",
                            "video_prompt": "v",
                            "generated_assets": _pending_assets(),
                        }
                    ],
                    "reference_units": [
                        {
                            "unit_id": "E1U01",
                            "shot_ids": ["E1S01"],
                            "references": [],
                            "generated_assets": _pending_assets(),
                        }
                    ],
                },
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("ad-ref")
        prev_meta = previous["scripts"]["episode_1.json"]
        # ad 骨架恒为 shots，但组合成立时快照额外记录 reference_units 的 video_clip。
        assert prev_meta["kind"] == "shots"
        assert prev_meta["reference_units"]["E1U01"]["video_clip"] == ""

        script = pm.load_script("ad-ref", "episode_1.json")
        script["reference_units"][0]["generated_assets"]["video_clip"] = "videos/E1U01.mp4"
        with project_change_source("filesystem"):
            pm.save_script("ad-ref", script, "episode_1.json", validate=False)
        current = service._build_snapshot("ad-ref")

        changes = service._diff_snapshots(previous, current)
        unit_ready = [c for c in changes if c["action"] == "video_ready" and c["entity_id"] == "E1U01"]
        assert len(unit_ready) == 1
        change = unit_ready[0]
        # 实体类型/名词/锚点复用 video_units 骨架条目，不新造平行枚举。
        assert change["entity_type"] == "reference_unit"
        assert change["label"].startswith("视频单元")
        assert change["focus"]["anchor_type"] == "reference_unit"
        assert change["focus"]["anchor_id"] == "E1U01"
        # shots 不承载该路径产物：不发 shot 级 video_ready。
        assert not any(c["entity_type"] == "shot" and c["action"] == "video_ready" for c in changes)

    def test_ad_reference_unit_redrive_does_not_emit_unit_events(self, tmp_path):
        """ad + reference_video：unit 增删/成员变化是 shots 编辑的派生回声，不产生 unit 级事件。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("ad-ref-redrive")
        pm.create_project_metadata(
            "ad-ref-redrive", "AdRef", "Anime", "ad", extras={"generation_mode": "reference_video"}
        )

        def _shot(shot_id: str) -> dict:
            return {
                "shot_id": shot_id,
                "duration_seconds": 4,
                "characters_in_shot": [],
                "scenes": [],
                "props": [],
                "image_prompt": "p",
                "video_prompt": "v",
                "generated_assets": _pending_assets(),
            }

        with project_change_source("filesystem"):
            pm.save_script(
                "ad-ref-redrive",
                {
                    "episode": 1,
                    "title": "广告",
                    "content_mode": "ad",
                    "shots": [_shot("E1S01")],
                    "reference_units": [
                        {
                            "unit_id": "E1U01",
                            "shot_ids": ["E1S01"],
                            "references": [],
                            "generated_assets": _pending_assets(),
                        }
                    ],
                },
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("ad-ref-redrive")

        # 重派生：既有 unit 成员变化 + 新增 unit，均无 video_clip。
        script = pm.load_script("ad-ref-redrive", "episode_1.json")
        script["shots"].append(_shot("E1S02"))
        script["reference_units"] = [
            {
                "unit_id": "E1U01",
                "shot_ids": ["E1S01", "E1S02"],
                "references": [],
                "generated_assets": _pending_assets(),
            },
            {"unit_id": "E1U02", "shot_ids": ["E1S02"], "references": [], "generated_assets": _pending_assets()},
        ]
        with project_change_source("filesystem"):
            pm.save_script("ad-ref-redrive", script, "episode_1.json", validate=False)
        current = service._build_snapshot("ad-ref-redrive")

        changes = service._diff_snapshots(previous, current)
        # unit 增删/成员变化不发 unit 级事件（内容变更由 shots 差分承载）。
        assert not any(c["entity_type"] == "reference_unit" for c in changes)

    def test_ad_storyboard_path_ignores_residual_reference_units(self, tmp_path):
        """generation_mode 非 reference_video 的 ad 项目：残留 reference_units 不发 unit 级事件，

        shots 级行为与现状一致（shots 承载产物，video_clip 空→非空发 shot 级 video_ready）。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("ad-sb")
        # 不设 generation_mode → effective_mode 回退默认 storyboard。
        pm.create_project_metadata("ad-sb", "AdSb", "Anime", "ad")

        with project_change_source("filesystem"):
            pm.save_script(
                "ad-sb",
                {
                    "episode": 1,
                    "title": "广告",
                    "content_mode": "ad",
                    "shots": [
                        {
                            "shot_id": "E1S01",
                            "duration_seconds": 4,
                            "characters_in_shot": [],
                            "scenes": [],
                            "props": [],
                            "image_prompt": "p",
                            "video_prompt": "v",
                            "generated_assets": _pending_assets(),
                        }
                    ],
                    # 残留派生索引：storyboard 路径不应据此发 unit 级事件。
                    "reference_units": [
                        {
                            "unit_id": "E1U01",
                            "shot_ids": ["E1S01"],
                            "references": [],
                            "generated_assets": _pending_assets(),
                        }
                    ],
                },
                "episode_1.json",
                validate=False,
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("ad-sb")
        # 组合不成立：快照不记录残留 reference_units。
        assert previous["scripts"]["episode_1.json"]["reference_units"] == {}

        script = pm.load_script("ad-sb", "episode_1.json")
        # shots 承载产物；残留 unit 也填上 video_clip（应被忽略）。
        script["shots"][0]["generated_assets"]["video_clip"] = "videos/E1S01.mp4"
        script["reference_units"][0]["generated_assets"]["video_clip"] = "videos/E1U01.mp4"
        with project_change_source("filesystem"):
            pm.save_script("ad-sb", script, "episode_1.json", validate=False)
        current = service._build_snapshot("ad-sb")

        changes = service._diff_snapshots(previous, current)
        # 残留索引不发 unit 级事件。
        assert not any(c["entity_type"] == "reference_unit" for c in changes)
        # storyboard 路径 shots 承载产物：shot 级 video_ready 正常。
        assert any(
            c["entity_type"] == "shot" and c["action"] == "video_ready" and c["entity_id"] == "E1S01" for c in changes
        )

    @pytest.mark.asyncio
    async def test_watch_terminates_stream_when_project_directory_deleted(self, tmp_path, caplog):
        """订阅存续期间删除项目目录：一个轮询周期内扫描终止——广播终止事件、
        流正常结束、通道从注册表移除，仅记一条 INFO 日志，无 ERROR/traceback。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        with caplog.at_level(logging.INFO, logger="server.services.project_events"):
            async with service.stream_events("demo", idle_timeout=0.1) as stream:
                first = await anext(stream)
                assert first[0] == "snapshot"

                pm.delete_project_directory("demo")

                event_name, payload = await _next_event(stream, timeout=1.5)
                assert event_name == PROJECT_DELETED_EVENT
                assert payload == {"project_name": "demo"}

                # 终止事件之后流正常结束（不是因为消费方主动断线）。
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

        with caplog.at_level(logging.INFO, logger="server.services.project_events"):
            async with service.stream_events("demo", idle_timeout=0.1):
                (pm.get_project_path("demo") / ProjectManager.PROJECT_FILE).unlink()
                # 等至少一个轮询周期，让扫描命中缺失的 project.json。
                await asyncio.sleep(0.3)
                assert "demo" in service._channels  # 通道未终止，仍在注册表中

        assert any(record.levelno >= logging.ERROR for record in caplog.records)
        assert not any("已被删除" in record.message for record in caplog.records)

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_hint_rebuild_terminates_channel_without_error_when_project_deleted(self, tmp_path, caplog):
        """hint 触发的显式重建路径对已删除项目同样走终止处理，不产生 ERROR 日志。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        # 轮询间隔调大，确保观察到的是 hint 路径而非后台轮询先行探测到删除。
        service = ProjectEventService(tmp_path, poll_interval=5.0)
        await service.start()

        with caplog.at_level(logging.INFO, logger="server.services.project_events"):
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

        # 同名项目重建：新订阅应走全新通道，正常收到 snapshot 与后续变更（不复用将死通道）。
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo Reborn", "Anime", "narration")

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            first = await anext(stream)
            assert first[0] == "snapshot"
            assert "demo" in service._channels

        await service.shutdown()
