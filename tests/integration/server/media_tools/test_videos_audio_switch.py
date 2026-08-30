"""Agent 视频入队路径上的音频开关预检。

WebUI 提交入口拒绝的配置（成片恒有声的模型 + 关闭音频），从 Agent 入队同样要被拒——放行会让
编排层按无声路径裁掉全部音色约束，用户拿到失去音色约束的有声成片。分镜图生视频复用
``server.services.video_caps``，参考生视频由公共 request projection 承载相同判据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.generation_queue_client import TaskSpec
from lib.generation_result import GenerationAction, GenerationSelectionMode
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.reference_video.request_projection import ReferenceRequestOptions
from lib.reference_video.text_parser import extract_mentions
from server.media_tools import videos as mod
from server.media_tools.context import ToolContext
from server.services import video_batch_admission as admission_mod
from server.services.video_batch_admission import admit_reference_video_batch
from server.services.video_caps import assert_audio_switch_supported
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import _videos_tool_for_scope

_ALWAYS_AUDIBLE = "dashscope/wan2.7-i2v"
_CONTROLLABLE = "ark/doubao-seedance-2-0-260128"


def _episode_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "episode")


def _selected_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "selected")


def _all_scope(ctx: ToolContext):
    return _videos_tool_for_scope(ctx, "all")


async def _seed_settings(factory: async_sessionmaker[AsyncSession], **settings: str) -> None:
    """把系统设置写进共享 DB fixture 的库里。"""
    async with factory() as session:
        svc = ConfigService(session)
        for key, value in settings.items():
            await svc.set_setting(key, value)
        await session.commit()


class _FakePM:
    def __init__(self, project: dict[str, Any]) -> None:
        self.project = project

    def load_project(self, _name: str) -> dict[str, Any]:
        return self.project


def _ctx(tmp_path: Path, project: dict[str, Any]) -> ToolContext:
    return ToolContext(
        project_name="demo",
        projects_root=tmp_path,
        pm=_FakePM(project),  # type: ignore[arg-type]
    )


def _unit_spec(unit: dict[str, Any]) -> TaskSpec:
    """可入队 unit 的替身 spec：只用于让逐桶去重判定「这条要入队」。"""
    return TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id=str(unit["unit_id"]),
        prompt="镜头",
        script_file="episode_1.json",
    )


class TestAssertAudioSwitchSupported:
    async def test_always_audible_model_with_audio_off_names_provider_and_model(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE, video_generate_audio="false")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        with pytest.raises(ValueError, match=r"成片恒有声，无法关闭音频") as exc_info:
            await assert_audio_switch_supported({}, "i2v")

        assert "dashscope/wan2.7-i2v" in str(exc_info.value)

    async def test_controllable_model_keeps_the_off_setting(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_CONTROLLABLE, video_generate_audio="false")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        await assert_audio_switch_supported({}, "i2v")

        # 闸门放行后关音设置原样生效，没有被改写成有声
        assert await ConfigResolver(db_factory).video_generate_audio_for_project({}) is False


class TestStoryboardRouteGate:
    """分镜图生视频：闸门与创作类型无关，但只在确有任务要入队时才拦。"""

    async def test_gate_is_content_mode_agnostic(self, tmp_path, monkeypatch):
        seen: list[str] = []

        async def _reject(_project, capability):
            seen.append(capability)
            raise ValueError("成片恒有声")

        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _reject)
        conflict = await admission_mod.audio_switch_conflict({"generation_mode": "storyboard"})
        assert conflict == "成片恒有声"
        assert seen == ["i2v"]

    async def test_voice_characters_resolve_independently_of_the_gate(self, tmp_path, monkeypatch):
        async def _not_silent(_project):
            return False

        monkeypatch.setattr(admission_mod, "resolve_project_is_silent", _not_silent)
        project = {"generation_mode": "storyboard", "characters": {"张三": {"description": "主角"}}}
        assert await admission_mod.resolve_voice_context(project, "drama") == project["characters"]


class TestReferenceRouteGate:
    """参考生视频：按本批真正要入队的 unit 调用公共 request projection。"""

    @staticmethod
    def _stub_current_state(monkeypatch) -> None:
        async def _no_active(**_kwargs):
            return []

        async def _passthrough_options(*, options, **_kwargs):
            return options

        monkeypatch.setattr(admission_mod, "get_active_tasks_for_resources", _no_active)
        monkeypatch.setattr(admission_mod, "prepare_current_reference_video_request_options", _passthrough_options)

    async def test_projects_each_pending_unit_and_skips_done_units(self, tmp_path, monkeypatch):
        seen: list[str] = []

        class _Projection:
            unit_id = "test"
            blocking_problems: tuple[object, ...] = ()
            cost = None
            planned_duration = 8
            request_duration = None
            current_visual_duration = None

            def to_advisory_payload(self):
                return {"allowed": True, "unit_id": "test", "problems": []}

        async def _record(*, unit, **_kwargs):
            # 替身只需产出可预期的分桶信号：本用例的 project 为空，未登记名经生产侧的
            # unit_reference_declarations 会被全部滤掉，故此处按正文提及直接判。
            seen.append("r2v" if extract_mentions(str(unit.get("text") or "")) else "i2v")
            return _Projection()

        self._stub_current_state(monkeypatch)
        monkeypatch.setattr(admission_mod, "project_reference_unit_request", _record)
        units = [
            {"unit_id": "E1U1", "text": "@[张三] 推门"},
            {"unit_id": "E1U2", "text": "@[李四] 举杯"},
            {"unit_id": "E1U3", "text": "空镜：长街"},
        ]
        await admit_reference_video_batch(
            project_name="demo",
            project={},
            project_path=tmp_path,
            script={"video_units": units},
            script_file="episode_1.json",
            units=units,
            request_options=ReferenceRequestOptions(),
            operation="generate_video",
            selection=GenerationSelectionMode.MISSING_ONLY,
            spec_check=_unit_spec,
        )
        assert seen == ["r2v", "r2v", "i2v"]

    async def test_units_that_cannot_be_enqueued_do_not_trigger_projection(self, tmp_path, monkeypatch):
        """不可入队的 unit 不该触发解析：它本就不会被生成，判定停在更早的拒绝上。"""
        called = False

        async def _record(**_kwargs):
            nonlocal called
            called = True

        def _reject(_unit):
            raise ValueError("正文为空")

        self._stub_current_state(monkeypatch)
        monkeypatch.setattr(admission_mod, "project_reference_unit_request", _record)
        admission = await admit_reference_video_batch(
            project_name="demo",
            project={},
            project_path=tmp_path,
            script={"video_units": []},
            script_file="episode_1.json",
            units=[{"unit_id": "E1U1", "text": ""}],
            request_options=ReferenceRequestOptions(),
            operation="generate_video",
            selection=GenerationSelectionMode.MISSING_ONLY,
            spec_check=_reject,
        )
        assert called is False
        assert not admission.admitted


def _claim_existing_video(project_dir: Path, resource_id: str) -> None:
    """落一段已产出的视频并在清单里登记它：清单是「这条已经做过」的唯一凭据。"""

    from lib.artifact_manifest import (
        ArtifactKey,
        ArtifactManifest,
        ArtifactManifestEntry,
        ProjectArtifactManifestAdapter,
    )
    from lib.resource_paths import resource_relative_path

    artifact_path = resource_relative_path("videos", resource_id)
    absolute = project_dir / artifact_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_bytes(b"rendered-video")
    ArtifactManifest(ProjectArtifactManifestAdapter(project_dir)).register_entry_transactionally(
        ArtifactKey.episode_video(1, resource_id),
        ArtifactManifestEntry(artifact_path=artifact_path, basis_digest="sha256-v1:" + "a" * 64),
    )


class _EpisodePM:
    """整集工具够用的 pm 替身：一集一个 segment，分镜图有无由调用方决定。

    项目按生产形态构造：当前 schema、剧本在 episodes 账本里绑定，已落盘的分镜图在构造时
    经清单激活登记——清单是读取已生成产物的唯一口径。构造之后用例会往内存剧本里塞畸形
    条目验证工具侧的逐条拒收，那些条目不回写磁盘，清单保持这份干净基线。
    """

    def __init__(self, project_dir: Path, *, with_storyboard: bool, with_video: bool = False) -> None:
        self._project_dir = project_dir
        item: dict[str, Any] = {
            "segment_id": "E1S01",
            "novel_text": "镜头缓缓扫过原野。",
            "image_prompt": "原野远景",
            "video_prompt": "镜头平移",
        }
        assets: dict[str, Any] = {}
        if with_storyboard:
            assets["storyboard_image"] = "storyboards/scene_E1S01.png"
        if with_video:
            from lib.resource_paths import resource_relative_path

            assets["video_clip"] = resource_relative_path("videos", "E1S01")
        if assets:
            item["generated_assets"] = assets
        self.script_payload: dict[str, Any] = {"content_mode": "narration", "episode": 1, "segments": [item]}
        self.project_payload: dict[str, Any] = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
        self._mirror()

    def get_project_path(self, _name: str) -> Path:
        return self._project_dir

    def _mirror(self) -> None:
        """把基线项目落盘并激活产物清单，等价于生产的迁移补录。"""

        from lib.artifact_activation import activate_artifact_target_state

        self._project_dir.mkdir(parents=True, exist_ok=True)
        (self._project_dir / "project.json").write_text(
            json.dumps(self.project_payload, ensure_ascii=False), encoding="utf-8"
        )
        scripts_dir = self._project_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "episode_1.json").write_text(
            json.dumps(self.script_payload, ensure_ascii=False), encoding="utf-8"
        )
        activate_artifact_target_state(self._project_dir, bump_schema=False)

    def load_project(self, _name: str) -> dict[str, Any]:
        return self.project_payload

    def load_script(self, _name: str, _filename: str) -> dict[str, Any]:
        return self.script_payload


class TestStoryboardGateSkipsEmptyBatches:
    """没有任务要入队时不触发闸门：存量的关闭音频配置不该把一次空转变成报错。"""

    def _ctx_with(self, tmp_path: Path, *, with_storyboard: bool, with_video: bool = False) -> ToolContext:
        project_dir = tmp_path / "demo"
        (project_dir / "storyboards").mkdir(parents=True)
        (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
        ctx = ToolContext(
            project_name="demo",
            projects_root=tmp_path,
            pm=_EpisodePM(project_dir, with_storyboard=with_storyboard, with_video=with_video),  # type: ignore[arg-type]
        )
        if with_video:
            _claim_existing_video(project_dir, "E1S01")
        return ctx

    async def _run_episode(self, ctx: ToolContext, monkeypatch, **args: Any) -> dict[str, Any]:
        rejected: list[str] = []

        async def _reject(_project, capability):
            rejected.append(capability)
            raise ValueError("成片恒有声")

        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _reject)
        tool_obj = _episode_scope(ctx)
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production", **args})
        return {"out": out, "rejected": rejected}

    async def test_all_items_filtered_out_still_fails_without_consulting_the_gate(self, tmp_path, monkeypatch):
        """全部条目缺分镜图时报的应是逐 ID 的输入不可用，而不是音频开关冲突。"""
        ctx = self._ctx_with(tmp_path, with_storyboard=False)

        result = await self._run_episode(ctx, monkeypatch)

        assert result["rejected"] == []
        assert result["out"].get("is_error") is True
        payload = result["out"]["generation_result"]
        assert payload["blocked"] == ["E1S01"]
        assert payload["items"][0]["problem"]["code"] == "generation_unit_input_unusable"


class TestStoryboardGateEntersAdmission:
    """音频开关冲突与其它缺口一起在建任务之前报全，不留到确认之后才报。"""

    @pytest.fixture(autouse=True)
    def _no_active_tasks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """准入要查活跃任务：单元测试不碰真实数据库，一律按「没有活跃任务」作答。"""

        async def _none(**_kwargs):
            return []

        monkeypatch.setattr(admission_mod, "get_active_tasks_for_resources", _none)

    def _ctx(self, tmp_path: Path) -> ToolContext:
        project_dir = tmp_path / "demo"
        (project_dir / "storyboards").mkdir(parents=True)
        (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
        return ToolContext(
            project_name="demo",
            projects_root=tmp_path,
            pm=_EpisodePM(project_dir, with_storyboard=True),  # type: ignore[arg-type]
        )

    async def test_audio_switch_conflict_is_reported_as_a_blocked_admission(self, tmp_path, monkeypatch):
        async def _reject(_project, _capability):
            raise ValueError("成片恒有声，无法关闭音频")

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _reject)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

        tool_obj = _episode_scope(self._ctx(tmp_path))
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production"})

        assert out["batch_admission"]["decision"] == "blocked"
        enqueue.assert_not_awaited()
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes == {"E1S01": ["video_audio_switch_not_supported"]}

    async def test_a_blank_prompt_is_refused_per_unit(self, tmp_path, monkeypatch):
        """空白提示词构造不出 TaskSpec：该条目带自己的问题码进结论，不把整批打成通用报错。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        ctx.pm.script_payload["segments"][0]["video_prompt"] = "   "  # type: ignore[attr-defined]

        tool_obj = _episode_scope(ctx)
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production"})

        enqueue.assert_not_awaited()
        assert out["batch_admission"]["decision"] == "blocked"
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes == {"E1S01": ["generation_unit_request_invalid"]}

    async def test_a_duplicate_item_id_is_refused_before_admission(self, tmp_path, monkeypatch):
        """同一个 id 在剧本里出现两次：副本被拒收，整批停在建任务之前。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        segments = ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
        segments.append({**segments[0]})

        tool_obj = _episode_scope(ctx)
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production"})

        enqueue.assert_not_awaited()
        assert out["batch_admission"]["decision"] == "blocked"
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes["E1S01#1"] == ["generation_unit_request_invalid"]

    async def test_the_audio_conflict_joins_the_other_problems_of_the_same_unit(self, tmp_path, monkeypatch):
        """音频冲突与投影侧的缺口写进同一张票：用户一次看全，不必改一条撞一条。"""

        from lib.batch_admission import BatchAdmission, refused_ticket

        async def _reject(_project, _capability):
            raise ValueError("成片恒有声，无法关闭音频")

        async def _admit(**kwargs: Any) -> BatchAdmission:
            return BatchAdmission(
                operation=kwargs["operation"],
                selection=kwargs["selection"],
                narration_delivery=kwargs["request_options"].narration_delivery,
                tickets=(
                    refused_ticket(
                        "E1S01",
                        code="reference_tts_stale",
                        detail="配音已过期",
                        action=GenerationAction.GENERATE_DEPENDENCY,
                    ),
                ),
            )

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _reject)
        monkeypatch.setattr(admission_mod, "admit_storyboard_video_batch", _admit)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

        tool_obj = _episode_scope(self._ctx(tmp_path))
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production"})

        enqueue.assert_not_awaited()
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes == {"E1S01": ["reference_tts_stale", "video_audio_switch_not_supported"]}

    async def test_a_non_scalar_item_id_is_refused_per_unit(self, tmp_path, monkeypatch):
        """id 写成数组的条目按位置记名拒收，不把整批打成一句通用报错。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        ctx.pm.script_payload["segments"][0]["segment_id"] = ["E1S01"]  # type: ignore[attr-defined]

        tool_obj = _episode_scope(ctx)
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production"})

        enqueue.assert_not_awaited()
        assert out["batch_admission"]["decision"] == "blocked"
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes == {"items[0]": ["generation_unit_request_invalid"]}

    async def test_a_non_object_entry_is_refused_per_unit(self, tmp_path, monkeypatch):
        """剧本里混进非对象条目：它按位置记名拒收，不把整批打成一句通用报错。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        ctx.pm.script_payload["segments"].insert(0, 42)  # type: ignore[attr-defined]

        tool_obj = _episode_scope(ctx)
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production"})

        enqueue.assert_not_awaited()
        assert out["batch_admission"]["decision"] == "blocked"
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes["items[0]"] == ["generation_unit_request_invalid"]

    async def test_a_diagnostic_name_never_shadows_a_real_id(self, tmp_path, monkeypatch):
        """按位置记的诊断名与剧本里某个真实 ID 撞上时另起一个名字：同名会让两条并成一条。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        segments = ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
        segments.insert(0, 42)
        segments[1]["segment_id"] = "items[0]"

        tool_obj = _episode_scope(ctx)
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production"})

        enqueue.assert_not_awaited()
        assert out["batch_admission"]["decision"] == "blocked"
        unit_ids = [unit["unit_id"] for unit in out["batch_admission"]["units"]]
        assert sorted(unit_ids) == ["items[0]", "items[0]*"]

    async def test_a_named_alias_pointing_at_two_items_is_refused(self, tmp_path, monkeypatch):
        """点名用的 scene_id 别名落在两个条目上：各入口会各自选中头一个或末一个，先拒收。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        segments = ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
        first = segments[0]
        first["scene_id"] = "SC1"
        segments.append({**first, "segment_id": "E1S02"})

        tool_obj = _selected_scope(ctx)
        out = await tool_obj.handler(
            {"script": "episode_1.json", "narration_delivery": "post_production", "scene_ids": ["SC1"]}
        )

        enqueue.assert_not_awaited()
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes["SC1"] == ["generation_unit_request_invalid"]

    async def test_a_non_scalar_alias_does_not_break_addressing(self, tmp_path, monkeypatch):
        """脏剧本把 scene_id 写成数组：按名字寻址前先判类型，该条目仍能按规范 ID 点名。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        segments = ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
        segments[0]["scene_id"] = ["E1S01"]

        tool_obj = _selected_scope(ctx)
        out = await tool_obj.handler(
            {"script": "episode_1.json", "narration_delivery": "post_production", "scene_ids": ["E1S01"]}
        )

        # 别名不可用不影响按规范 ID 寻址：不崩、不塌成一句通用报错，该目标照常进入这批。
        assert out.get("is_error") is not True, out
        assert [spec.resource_id for spec in enqueue.await_args.kwargs["specs"]] == ["E1S01"]

    async def test_two_aliases_over_one_canonical_id_are_refused(self, tmp_path, monkeypatch):
        """两个条目共用规范 ID、别名各不相同：按别名点名同样无从判定要做哪一条。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        segments = ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
        segments[0]["scene_id"] = "A"
        segments.append({**segments[0], "scene_id": "B"})

        tool_obj = _selected_scope(ctx)
        out = await tool_obj.handler(
            {"script": "episode_1.json", "narration_delivery": "post_production", "scene_ids": ["B"]}
        )

        enqueue.assert_not_awaited()
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes["B"] == ["generation_unit_request_invalid"]

    async def test_a_non_scalar_canonical_id_is_not_masked_by_an_alias(self, tmp_path, monkeypatch):
        """规范 ID 写成 0、别名却写得好好的：不能让别名替它蒙混过筛查。

        执行期只按规范字段定位目标，这条按别名入队的话谁也做不出来，而同批健康的兄弟条目
        已经入队计费。
        """

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        segments = ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
        segments[0]["segment_id"] = 0
        segments[0]["scene_id"] = "E1S01"

        tool_obj = _selected_scope(ctx)
        out = await tool_obj.handler(
            {"script": "episode_1.json", "narration_delivery": "post_production", "scene_ids": ["E1S01"]}
        )

        enqueue.assert_not_awaited()
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes["E1S01"] == ["generation_unit_request_invalid"]

    async def test_generate_all_keeps_an_id_less_item_in_the_verdict(self, tmp_path, monkeypatch):
        """缺 ID 的条目进不了目标集合，但它属于这次请求：健康的兄弟条目不会独自入队计费。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        segments = ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
        segments.append({**segments[0], "segment_id": ""})

        tool_obj = _all_scope(ctx)
        out = await tool_obj.handler({"script": "episode_1.json", "narration_delivery": "post_production"})

        enqueue.assert_not_awaited()
        assert out["batch_admission"]["decision"] == "blocked"
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        assert codes["items[1]"] == ["generation_unit_request_invalid"]
        assert codes["E1S01"] == ["generation_batch_admission_withheld"]

    async def test_selected_rejects_a_duplicate_of_the_named_id(self, tmp_path, monkeypatch):
        """点名的 ID 在剧本里有两份：无法判定要做哪一条，整批停在建任务之前。"""

        async def _allow(_project, _capability):
            return None

        enqueue = AsyncMock(return_value=([], []))
        monkeypatch.setattr(admission_mod, "assert_audio_switch_supported", _allow)
        monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)
        ctx = self._ctx(tmp_path)
        segments = ctx.pm.script_payload["segments"]  # type: ignore[attr-defined]
        segments.append({**segments[0]})

        tool_obj = _selected_scope(ctx)
        out = await tool_obj.handler(
            {"script": "episode_1.json", "narration_delivery": "post_production", "scene_ids": ["E1S01"]}
        )

        enqueue.assert_not_awaited()
        codes = {
            unit["unit_id"]: [problem["code"] for problem in unit["problems"]]
            for unit in out["batch_admission"]["units"]
        }
        # 结论记在用户点的那个名字上：他要的是 E1S01，得到的是「这个名字指向多个条目」。
        assert codes["E1S01"] == ["generation_unit_request_invalid"]
