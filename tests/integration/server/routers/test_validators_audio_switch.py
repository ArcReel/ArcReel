"""视频生成入口预检 ``require_audio_switch_supported`` 的行为：

恒有声模型（请求里没有音轨开关可下发）遇到「关闭音频」的配置时在提交入口即拒绝——放行会让
编排层按无声路径裁掉全部音色约束，用户拿到的是失去音色约束的有声成片。开关可控的模型、
以及解析不出模型的场景一律放行。
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.service import ConfigService
from lib.infra.api_errors import BadRequestError
from server.routers._validators import require_audio_switch_supported
from tests.fakes import bind_session_factory
from tests.http_capture import capture_http

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
        bind_session_factory(monkeypatch, db_factory)

        with pytest.raises(BadRequestError) as exc_info:
            await require_audio_switch_supported({}, "i2v")

        assert exc_info.value.key == "video_audio_switch_not_supported"
        assert exc_info.value.params == {"provider": "dashscope", "model": "wan2.7-i2v"}

    async def test_always_audible_model_rejects_project_level_off_override(self, db_factory, monkeypatch):
        """项目覆盖优先于全局：全局开着、项目关掉，同样在入口拒绝。"""
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE, video_generate_audio="true")
        bind_session_factory(monkeypatch, db_factory)

        with pytest.raises(BadRequestError):
            await require_audio_switch_supported({"video_generate_audio": False}, "i2v")

    async def test_always_audible_model_passes_when_audio_is_on(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE)
        bind_session_factory(monkeypatch, db_factory)

        assert await require_audio_switch_supported({}, "i2v") is None

    async def test_controllable_model_keeps_the_off_setting(self, db_factory, monkeypatch):
        """开关可控的供应商行为不变：关闭意图能抵达请求，无声路径照常成立。"""
        await _seed_settings(db_factory, default_video_backend=_CONTROLLABLE, video_generate_audio="false")
        bind_session_factory(monkeypatch, db_factory)

        assert await require_audio_switch_supported({}, "i2v") is None

    @pytest.mark.parametrize(
        ("endpoint", "audio_track", "rejected"),
        [
            ("ark-seedance-2", "always_on", False),
            ("ark-seedance-2", "controllable", False),
            ("dashscope-video", None, True),
            ("openai-video", None, True),
            ("openai-video", "always_off", False),
            ("dashscope-video", "controllable", True),
            ("gemini-interactions-video", None, True),
            ("kling-video-model-path", None, True),
            ("minimax-h3", None, True),
            ("minimax-hailuo-v1", None, False),
            ("dashscope-video", "always_off", False),
            ("ark-seedance-2", None, False),
        ],
    )
    async def test_custom_model_keeps_endpoint_audio_facts_at_admission(
        self, db_factory, monkeypatch, endpoint, audio_track, rejected
    ):
        """端点声明与模型覆盖一起决定准入；无开关端点的关闭请求在任何 HTTP 前被拒。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            provider = CustomProvider(
                display_name="Relay", discovery_format="openai", base_url="https://relay.test", api_key="k"
            )
            session.add(provider)
            await session.flush()
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id="relay-video-x1",
                    display_name="relay-video-x1",
                    endpoint=endpoint,
                    capability_overrides={"durations": [5], **({"audio_track": audio_track} if audio_track else {})},
                )
            )
            await session.commit()
            backend = f"custom-{provider.id}/relay-video-x1"
        await _seed_settings(db_factory, default_video_backend=backend, video_generate_audio="false")
        bind_session_factory(monkeypatch, db_factory)

        with capture_http() as http:
            if rejected:
                with pytest.raises(BadRequestError) as exc_info:
                    await require_audio_switch_supported({}, "i2v")
                assert exc_info.value.key == "video_audio_switch_not_supported"
            else:
                assert await require_audio_switch_supported({}, "i2v") is None
            assert not http.calls

    async def test_no_provider_configured_passes_through(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, video_generate_audio="false")
        bind_session_factory(monkeypatch, db_factory)

        assert await require_audio_switch_supported({}, "i2v") is None


class TestResolutionFailurePassesThrough:
    """事实求值的数据库故障不升级为音频开关拒绝。"""

    async def test_facts_evaluation_db_failure(self, monkeypatch):
        async def _fail(*_args, **_kwargs):
            raise SQLAlchemyError("db down")

        monkeypatch.setattr("server.services.tasks.video_caps.read_video_request_facts", _fail)

        assert await require_audio_switch_supported({}, "i2v") is None
