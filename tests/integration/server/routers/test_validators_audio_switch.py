"""视频生成入口预检 ``require_audio_switch_supported`` 的行为：

恒有声模型（请求里没有音轨开关可下发）遇到「关闭音频」的配置时在提交入口即拒绝——放行会让
编排层按无声路径裁掉全部音色约束，用户拿到的是失去音色约束的有声成片。开关可控的模型、
以及解析不出模型的场景一律放行。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.api_errors import BadRequestError
from lib.config.service import ConfigService
from server.routers._validators import require_audio_switch_supported

_ALWAYS_AUDIBLE = "dashscope/wan2.7-i2v"
_CONTROLLABLE = "ark/doubao-seedance-2-0-260128"


async def _seed_settings(factory: async_sessionmaker[AsyncSession], **settings: str) -> None:
    async with factory() as session:
        svc = ConfigService(session)
        for key, value in settings.items():
            await svc.set_setting(key, value)
        await session.commit()


class TestRequireAudioSwitchSupported:
    async def test_always_audible_model_rejects_stored_off_setting(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE, video_generate_audio="false")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        with pytest.raises(BadRequestError) as exc_info:
            await require_audio_switch_supported({}, "i2v")

        assert exc_info.value.key == "video_audio_switch_not_supported"
        assert exc_info.value.params == {"provider": "dashscope", "model": "wan2.7-i2v"}

    async def test_always_audible_model_rejects_project_level_off_override(self, db_factory, monkeypatch):
        """项目覆盖优先于全局：全局开着、项目关掉，同样在入口拒绝。"""
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE, video_generate_audio="true")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        with pytest.raises(BadRequestError):
            await require_audio_switch_supported({"video_generate_audio": False}, "i2v")

    async def test_always_audible_model_passes_when_audio_is_on(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE)
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        # 预检放行即静默返回 None
        assert await require_audio_switch_supported({}, "i2v") is None

    async def test_controllable_model_keeps_the_off_setting(self, db_factory, monkeypatch):
        """开关可控的供应商行为不变：关闭意图能抵达请求，无声路径照常成立。"""
        await _seed_settings(db_factory, default_video_backend=_CONTROLLABLE, video_generate_audio="false")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        assert await require_audio_switch_supported({}, "i2v") is None

    async def test_no_provider_configured_passes_through(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, video_generate_audio="false")
        monkeypatch.setattr("lib.db.async_session_factory", db_factory)

        assert await require_audio_switch_supported({}, "i2v") is None


class TestResolutionFailurePassesThrough:
    """两次解析都读库；任一次数据库故障都退化成放行，不把配置解析问题升级为提交期拒绝。"""

    def _install_resolver(self, monkeypatch, *, backend_exc=None, audio_exc=None):
        class _FakeResolver:
            def __init__(self, _factory):
                pass

            async def resolve_video_backend(self, _project, _model, *, capability):
                if backend_exc is not None:
                    raise backend_exc
                return SimpleNamespace(provider_id="dashscope", model_id="wan2.7-i2v")

            async def video_generate_audio_for_project(self, _project):
                if audio_exc is not None:
                    raise audio_exc
                return False

        monkeypatch.setattr("server.services.video_caps.ConfigResolver", _FakeResolver)

    async def test_backend_resolution_db_failure(self, monkeypatch):
        self._install_resolver(monkeypatch, backend_exc=SQLAlchemyError("db down"))

        assert await require_audio_switch_supported({}, "i2v") is None

    async def test_audio_setting_db_failure(self, monkeypatch):
        """恒有声模型已解析出来，但读音频开关时数据库故障——同样放行。"""
        self._install_resolver(monkeypatch, audio_exc=SQLAlchemyError("db down"))

        assert await require_audio_switch_supported({}, "i2v") is None
