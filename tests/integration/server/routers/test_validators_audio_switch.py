"""Video-entry ``require_audio_switch_supported`` preflight:

Always-audible models without outgoing switches reject audio-off at submission; admitting would
strip voice constraints through silent orchestration, returning audible outputs without requested voices. Controllable models
and unresolved model cases all pass.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.service import ConfigService
from lib.infra.api_errors import BadRequestError
from server.routers._validators import require_audio_switch_supported
from tests.fakes import bind_session_factory

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
        """Project overrides precede globals: globally on/project off likewise rejects at entry."""
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE, video_generate_audio="true")
        bind_session_factory(monkeypatch, db_factory)

        with pytest.raises(BadRequestError):
            await require_audio_switch_supported({"video_generate_audio": False}, "i2v")

    async def test_always_audible_model_passes_when_audio_is_on(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, default_video_backend=_ALWAYS_AUDIBLE)
        bind_session_factory(monkeypatch, db_factory)

        # Admitted preflight silently returns None.
        assert await require_audio_switch_supported({}, "i2v") is None

    async def test_controllable_model_keeps_the_off_setting(self, db_factory, monkeypatch):
        """Controllable providers preserve behavior: off intentions reach requests, supporting silent routes."""
        await _seed_settings(db_factory, default_video_backend=_CONTROLLABLE, video_generate_audio="false")
        bind_session_factory(monkeypatch, db_factory)

        assert await require_audio_switch_supported({}, "i2v") is None

    @pytest.mark.parametrize(("audio_track", "rejected"), [("always_on", True), ("controllable", False)])
    async def test_custom_model_follows_its_audio_track_override(self, db_factory, monkeypatch, audio_track, rejected):
        """Custom models use overridden audio forms; always-audible overrides reject like built-ins."""
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
                    endpoint="ark-seedance-2",
                    capability_overrides={"audio_track": audio_track},
                )
            )
            await session.commit()
            backend = f"custom-{provider.id}/relay-video-x1"
        await _seed_settings(db_factory, default_video_backend=backend, video_generate_audio="false")
        bind_session_factory(monkeypatch, db_factory)

        if rejected:
            with pytest.raises(BadRequestError) as exc_info:
                await require_audio_switch_supported({}, "i2v")
            assert exc_info.value.key == "video_audio_switch_not_supported"
        else:
            assert await require_audio_switch_supported({}, "i2v") is None

    async def test_no_provider_configured_passes_through(self, db_factory, monkeypatch):
        await _seed_settings(db_factory, video_generate_audio="false")
        bind_session_factory(monkeypatch, db_factory)

        assert await require_audio_switch_supported({}, "i2v") is None


class TestResolutionFailurePassesThrough:
    """Fact-evaluation DB failures never escalate into audio-switch rejection."""

    async def test_facts_evaluation_db_failure(self, monkeypatch):
        async def _fail(*_args, **_kwargs):
            raise SQLAlchemyError("db down")

        monkeypatch.setattr("server.services.tasks.video_caps.evaluate_video_request_facts", _fail)

        assert await require_audio_switch_supported({}, "i2v") is None
