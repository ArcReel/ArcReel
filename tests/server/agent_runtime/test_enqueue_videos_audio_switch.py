"""智能体视频入队路径上的音频开关预检。

WebUI 提交入口拒绝的配置（成片恒有声的模型 + 关闭音频），从智能体入队同样要被拒——放行会让
编排层按无声路径裁掉全部音色约束，用户拿到失去音色约束的有声成片。判据与路由入口同源
（``server.services.video_caps.resolve_audio_switch_conflict``），本文件覆盖智能体侧的接线：
两条路线各自的闸门位置、参考路线的逐桶去重、以及冲突时抛出的消息。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.config.service import ConfigService
from lib.db.base import Base
from server.agent_runtime.sdk_tools import enqueue_videos as mod
from server.agent_runtime.sdk_tools._context import ToolContext
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
    """分镜路线：闸门在 ``_resolve_voice_context``，先于 drama 分支——非 drama 项目同样被拦。"""

    async def test_gate_runs_before_the_drama_branch(self, tmp_path, monkeypatch):
        seen: list[str] = []

        async def _reject(_project, capability):
            seen.append(capability)
            raise ValueError("成片恒有声")

        monkeypatch.setattr(mod, "assert_audio_switch_supported", _reject)
        with pytest.raises(ValueError):
            await mod._resolve_voice_context(_ctx(tmp_path, {"generation_mode": "storyboard"}), "narration")
        assert seen == ["i2v"]

    async def test_gate_passes_through_to_voice_characters(self, tmp_path, monkeypatch):
        async def _pass(_project, _capability):
            return None

        async def _not_silent(_project):
            return False

        monkeypatch.setattr(mod, "assert_audio_switch_supported", _pass)
        monkeypatch.setattr(mod, "resolve_project_is_silent", _not_silent)
        project = {"generation_mode": "storyboard", "characters": {"张三": {"description": "主角"}}}
        assert await mod._resolve_voice_context(_ctx(tmp_path, project), "drama") == project["characters"]


@pytest.mark.unit
class TestReferenceRouteGate:
    """参考路线：按本批真正要入队的 unit 逐桶检查，同一桶只问一次。"""

    async def test_checks_each_bucket_once_and_skips_done_units(self, monkeypatch):
        seen: list[str] = []

        async def _record(_project, capability):
            seen.append(capability)

        monkeypatch.setattr(mod, "assert_audio_switch_supported", _record)
        units = [
            {"unit_id": "E1U1", "references": ["characters/张三.png"]},
            {"unit_id": "E1U2", "references": ["characters/李四.png"]},
            {"unit_id": "E1U3", "references": []},
            {"unit_id": "E1U4", "references": []},
        ]
        await mod._assert_audio_switch_for_units(
            project={},
            units=units,
            skip_ids={"E1U4"},
            spec_for=lambda _u: None,  # type: ignore[arg-type,return-value]
            ad_shots_for=None,
        )
        assert sorted(seen) == ["i2v", "r2v"]

    async def test_units_that_cannot_be_enqueued_do_not_trigger_resolution(self, monkeypatch):
        """不可入队的 unit 不该触发解析：它本就不会被生成，为它拒绝整批是失实的。"""
        called = False

        async def _record(_project, _capability):
            nonlocal called
            called = True

        def _reject(_unit):
            raise ValueError("没有 shots")

        monkeypatch.setattr(mod, "assert_audio_switch_supported", _record)
        await mod._assert_audio_switch_for_units(
            project={},
            units=[{"unit_id": "E1U1", "references": []}],
            skip_ids=set(),
            spec_for=_reject,
            ad_shots_for=None,
        )
        assert called is False
