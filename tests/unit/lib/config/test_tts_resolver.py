"""Audio resolution: resolve_audio_backend payload > project > global default / auto, plus new-TTS-project prefills."""

from __future__ import annotations

from lib.config.resolver import ConfigResolver, ProviderModel
from lib.config.service import ProviderStatus


def _ready(name: str, media_types: list[str]) -> ProviderStatus:
    return ProviderStatus(
        name=name,
        display_name=name,
        status="ready",
        media_types=media_types,
        capabilities=[],
        required_keys=[],
        credential_count=0,
    )


class _FakeSvc:
    def __init__(self, *, settings: dict[str, str] | None = None, ready: list[ProviderStatus] | None = None):
        self._settings = settings or {}
        self._ready = ready

    async def get_setting(self, key: str, default: str = "") -> str:
        return self._settings.get(key, default)

    async def get_all_settings(self) -> dict[str, str]:
        return dict(self._settings)

    async def get_all_providers_status(self) -> list[ProviderStatus]:
        if self._ready is not None:
            return self._ready
        return [_ready("dashscope", ["audio"])]


class TestResolveAudioProviderModel:
    async def test_payload_wins(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        result = await resolver._resolve_audio_provider_model(
            _FakeSvc(),
            None,
            {"audio_backend": "dashscope/qwen3-tts-flash"},
            {"audio_provider": "dashscope", "audio_model": "qwen3-tts-flash"},
        )
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")

    async def test_payload_untrusted_provider_ignored(self):
        # Untrusted unknown providers fall back to project.
        resolver = ConfigResolver.__new__(ConfigResolver)
        result = await resolver._resolve_audio_provider_model(
            _FakeSvc(),
            None,
            {"audio_backend": "dashscope/qwen3-tts-flash"},
            {"audio_provider": "totally-unknown", "audio_model": "x"},
        )
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")

    async def test_project_override(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        result = await resolver._resolve_audio_provider_model(
            _FakeSvc(),
            None,
            {"audio_backend": "dashscope/qwen3-tts-flash"},
            None,
        )
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")

    async def test_falls_back_to_global_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        svc = _FakeSvc(settings={"default_audio_backend": "dashscope/qwen3-tts-flash"})
        result = await resolver._resolve_audio_provider_model(svc, None, None, None)
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")

    async def test_falls_back_to_auto_resolve(self):
        # Without payload/project/global setting, auto-resolve chooses the first ready audio-capable provider.
        resolver = ConfigResolver.__new__(ConfigResolver)
        svc = _FakeSvc(settings={}, ready=[_ready("dashscope", ["audio"])])
        result = await resolver._resolve_audio_provider_model(svc, None, None, None)
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")


class TestResolveDefaultAudioBackend:
    async def test_global_setting_parsed(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        svc = _FakeSvc(settings={"default_audio_backend": "dashscope/qwen3-tts-flash"})
        assert await resolver._resolve_default_audio_backend(svc, None) == ("dashscope", "qwen3-tts-flash")

    async def test_empty_setting_auto_resolves(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        svc = _FakeSvc(settings={}, ready=[_ready("dashscope", ["audio"])])
        assert await resolver._resolve_default_audio_backend(svc, None) == ("dashscope", "qwen3-tts-flash")


class TestDefaultNarrationTts:
    """Global audio defaults, voice, and rate only prefill newly created TTS projects."""

    async def test_reads_global_settings(self, db_factory):
        from lib.config.service import ConfigService

        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await svc.set_setting("narration_voice", "Ethan")
            await svc.set_setting("narration_speed", "1.2")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        assert await resolver.default_narration_tts() == (
            ProviderModel("dashscope", "qwen3-tts-flash"),
            "Ethan",
            1.2,
        )

    async def test_unset_voice_and_speed_use_service_defaults(self, db_factory):
        from lib.config.service import ConfigService

        async with db_factory() as session:
            await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        _backend, voice, speed = await resolver.default_narration_tts()
        assert voice == "Cherry"
        assert speed is None


class TestPublicAudioResolverApi:
    async def test_default_audio_backend_reads_global_setting(self, db_factory):
        from lib.config.service import ConfigService

        async with db_factory() as session:
            await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        assert await resolver.default_audio_backend() == ("dashscope", "qwen3-tts-flash")

    async def test_resolve_audio_backend_payload_short_circuit(self, db_factory):
        resolver = ConfigResolver(db_factory)
        result = await resolver.resolve_audio_backend(
            None, {"audio_provider": "dashscope", "audio_model": "qwen3-tts-flash"}
        )
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")


class TestServiceDefaultAudioBackend:
    async def test_falls_back_to_builtin_default(self, db_factory):
        from lib.config.service import ConfigService

        async with db_factory() as session:
            svc = ConfigService(session)
            assert await svc.get_default_audio_backend() == ("dashscope", "qwen3-tts-flash")


class TestServiceNarrationVoice:
    async def test_blank_setting_falls_back_to_default(self, db_factory):
        # Blank global settings fall back to defaults, matching project-override stripping.
        from lib.config.service import ConfigService

        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_setting("narration_voice", "  ")
            await session.commit()
            assert await svc.get_narration_voice() == "Cherry"

            await svc.set_setting("narration_voice", "Ethan")
            await session.commit()
            assert await svc.get_narration_voice() == "Ethan"
