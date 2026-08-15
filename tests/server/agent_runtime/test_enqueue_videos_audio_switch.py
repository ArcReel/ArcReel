"""智能体视频入队路径上的音频开关预检。

WebUI 提交入口拒绝的配置（成片恒有声的模型 + 关闭音频），从智能体入队同样要被拒——放行会让
编排层按无声路径裁掉全部音色约束，用户拿到失去音色约束的有声成片。分镜路线复用
``server.services.video_caps``，参考路线由公共 request projection 承载相同判据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.config.service import ConfigService
from lib.db.base import Base
from lib.generation_queue_client import TaskSpec
from lib.generation_result import GenerationSelectionMode
from lib.reference_video.request_projection import ReferenceRequestOptions
from server.agent_runtime.sdk_tools import enqueue_videos as mod
from server.agent_runtime.sdk_tools._context import ToolContext
from server.services import video_batch_admission as admission_mod
from server.services.video_batch_admission import admit_reference_video_batch
from server.services.video_caps import assert_audio_switch_supported

_ALWAYS_AUDIBLE = "dashscope/wan2.7-i2v"
_CONTROLLABLE = "ark/doubao-seedance-2-0-260128"


async def _make_factory(**settings: str):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    if settings:
        async with factory() as session:
            svc = ConfigService(session)
            for key, value in settings.items():
                await svc.set_setting(key, value)
            await session.commit()
    return factory, engine


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


@pytest.mark.integration
class TestAssertAudioSwitchSupported:
    async def test_always_audible_model_with_audio_off_names_provider_and_model(self, monkeypatch):
        factory, engine = await _make_factory(default_video_backend=_ALWAYS_AUDIBLE, video_generate_audio="false")
        try:
            monkeypatch.setattr("lib.db.async_session_factory", factory)
            with pytest.raises(ValueError) as exc_info:
                await assert_audio_switch_supported({}, "i2v")
        finally:
            await engine.dispose()
        assert "dashscope/wan2.7-i2v" in str(exc_info.value)

    async def test_controllable_model_keeps_the_off_setting(self, monkeypatch):
        factory, engine = await _make_factory(default_video_backend=_CONTROLLABLE, video_generate_audio="false")
        try:
            monkeypatch.setattr("lib.db.async_session_factory", factory)
            await assert_audio_switch_supported({}, "i2v")
        finally:
            await engine.dispose()


@pytest.mark.unit
class TestStoryboardRouteGate:
    """分镜路线：闸门与内容模式无关，但只在确有任务要入队时才拦。"""

    async def test_gate_is_content_mode_agnostic(self, tmp_path, monkeypatch):
        seen: list[str] = []

        async def _reject(_project, capability):
            seen.append(capability)
            raise ValueError("成片恒有声")

        monkeypatch.setattr(mod, "assert_audio_switch_supported", _reject)
        with pytest.raises(ValueError):
            await mod._assert_audio_switch_for_storyboard(_ctx(tmp_path, {"generation_mode": "storyboard"}))
        assert seen == ["i2v"]

    async def test_voice_characters_resolve_independently_of_the_gate(self, tmp_path, monkeypatch):
        async def _not_silent(_project):
            return False

        monkeypatch.setattr(mod, "resolve_project_is_silent", _not_silent)
        project = {"generation_mode": "storyboard", "characters": {"张三": {"description": "主角"}}}
        assert await mod._resolve_voice_context(_ctx(tmp_path, project), "drama") == project["characters"]


@pytest.mark.unit
class TestReferenceRouteGate:
    """参考路线：按本批真正要入队的 unit 调用公共 request projection。"""

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
            seen.append("r2v" if unit.get("references") else "i2v")
            return _Projection()

        self._stub_current_state(monkeypatch)
        monkeypatch.setattr(admission_mod, "project_reference_unit_request", _record)
        units = [
            {"unit_id": "E1U1", "references": [{"type": "character", "name": "张三"}]},
            {"unit_id": "E1U2", "references": [{"type": "character", "name": "李四"}]},
            {"unit_id": "E1U3", "references": []},
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
            raise ValueError("没有 shots")

        self._stub_current_state(monkeypatch)
        monkeypatch.setattr(admission_mod, "project_reference_unit_request", _record)
        admission = await admit_reference_video_batch(
            project_name="demo",
            project={},
            project_path=tmp_path,
            script={"video_units": []},
            script_file="episode_1.json",
            units=[{"unit_id": "E1U1", "references": []}],
            request_options=ReferenceRequestOptions(),
            operation="generate_video",
            selection=GenerationSelectionMode.MISSING_ONLY,
            spec_check=_reject,
        )
        assert called is False
        assert not admission.admitted


class _EpisodePM:
    """整集工具够用的 pm 替身：一集一个 segment，分镜图有无由调用方决定。"""

    def __init__(self, project_dir: Path, *, with_storyboard: bool) -> None:
        self._project_dir = project_dir
        item: dict[str, Any] = {
            "segment_id": "E1S01",
            "novel_text": "镜头缓缓扫过原野。",
            "video_prompt": "镜头平移",
        }
        if with_storyboard:
            item["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        self.script_payload: dict[str, Any] = {"content_mode": "narration", "episode": 1, "segments": [item]}

    def get_project_path(self, _name: str) -> Path:
        return self._project_dir

    def load_project(self, _name: str) -> dict[str, Any]:
        return {"generation_mode": "storyboard"}

    def load_script(self, _name: str, _filename: str) -> dict[str, Any]:
        return self.script_payload


@pytest.mark.unit
class TestStoryboardGateSkipsEmptyBatches:
    """没有任务要入队时不触发闸门：存量的关闭音频配置不该把一次空转变成报错。"""

    def _ctx_with(self, tmp_path: Path, *, with_storyboard: bool) -> ToolContext:
        project_dir = tmp_path / "demo"
        (project_dir / "storyboards").mkdir(parents=True)
        (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
        return ToolContext(
            project_name="demo",
            projects_root=tmp_path,
            pm=_EpisodePM(project_dir, with_storyboard=with_storyboard),  # type: ignore[arg-type]
        )

    async def _run_episode(self, ctx: ToolContext, monkeypatch, **args: Any) -> dict[str, Any]:
        rejected: list[str] = []

        async def _reject(_project, capability):
            rejected.append(capability)
            raise ValueError("成片恒有声")

        monkeypatch.setattr(mod, "assert_audio_switch_supported", _reject)
        tool_obj = mod.generate_video_episode_tool(ctx)
        out = await tool_obj.handler({"script": "episode_1.json", **args})
        return {"out": out, "rejected": rejected}

    async def test_resume_with_everything_done_reports_completion(self, tmp_path, monkeypatch):
        ctx = self._ctx_with(tmp_path, with_storyboard=True)
        videos_dir = tmp_path / "demo" / "videos"
        videos_dir.mkdir(parents=True)
        (videos_dir / "scene_E1S01.mp4").write_bytes(b"")
        mod._save_checkpoint_at(videos_dir / ".checkpoint_ep1.json", ["E1S01"], "2026-01-01T00:00:00+00:00", episode=1)

        result = await self._run_episode(ctx, monkeypatch, resume=True)

        assert result["rejected"] == []
        assert result["out"].get("is_error") is not True

    async def test_all_items_filtered_out_still_fails_without_consulting_the_gate(self, tmp_path, monkeypatch):
        """全部条目缺分镜图时报的应是逐 ID 的输入不可用，而不是音频开关冲突。"""
        ctx = self._ctx_with(tmp_path, with_storyboard=False)

        result = await self._run_episode(ctx, monkeypatch)

        assert result["rejected"] == []
        assert result["out"].get("is_error") is True
        payload = result["out"]["generation_result"]
        assert payload["blocked"] == ["E1S01"]
        assert payload["items"][0]["problem"]["code"] == "generation_unit_input_unusable"
