"""音频 provider 解析：resolve_audio_backend（payload > project > 全局默认 / auto）与新建 TTS 项目的预填值。"""

from __future__ import annotations

from lib.config.resolver import ConfigResolver, ProviderModel
from lib.config.service import ProviderStatus


def _ready(name: str, media_types: list[str]) -> ProviderStatus:
    return ProviderStatus(
        name=name,
        display_name=name,
        description="",
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
        # 未知 provider 不予信任 → 回退 project
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
        # 无 payload / project / 全局设置 → auto-resolve 挑首个 ready 且支持 audio 的 provider
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
    """全局音频默认、音色与语速只作为新建 TTS 项目的预填值。"""

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
        # 全局 setting 被保存成空白时回退默认值，与项目级覆盖的 strip 语义一致
        from lib.config.service import ConfigService

        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_setting("narration_voice", "  ")
            await session.commit()
            assert await svc.get_narration_voice() == "Cherry"

            await svc.set_setting("narration_voice", "Ethan")
            await session.commit()
            assert await svc.get_narration_voice() == "Ethan"
