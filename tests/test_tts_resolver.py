"""音频 provider 解析：resolve_audio_backend（payload > project > 全局默认 / auto）+ resolve_narration_voice。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.config.resolver import ConfigResolver, ProviderModel
from lib.config.service import ProviderStatus
from lib.db.base import Base


def _ready(name: str, media_types: list[str]) -> ProviderStatus:
    return ProviderStatus(
        name=name,
        display_name=name,
        description="",
        status="ready",
        media_types=media_types,
        capabilities=[],
        required_keys=[],
        configured_keys=[],
        missing_keys=[],
    )


class _FakeSvc:
    def __init__(self, *, settings: dict[str, str] | None = None, ready: list[ProviderStatus] | None = None):
        self._settings = settings or {}
        self._ready = ready

    async def get_setting(self, key: str, default: str = "") -> str:
        return self._settings.get(key, default)

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


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


class TestResolveNarrationVoice:
    async def test_project_override_wins(self):
        factory, engine = await _make_factory()
        try:
            resolver = ConfigResolver(factory)
            assert await resolver.resolve_narration_voice({"narration_voice": "Ethan"}) == "Ethan"
        finally:
            await engine.dispose()

    async def test_default_when_no_override(self):
        factory, engine = await _make_factory()
        try:
            resolver = ConfigResolver(factory)
            assert await resolver.resolve_narration_voice(None) == "Cherry"
            assert await resolver.resolve_narration_voice({}) == "Cherry"
            # 空白覆盖不算覆盖
            assert await resolver.resolve_narration_voice({"narration_voice": "  "}) == "Cherry"
        finally:
            await engine.dispose()


class TestPublicAudioResolverApi:
    async def test_default_audio_backend_reads_global_setting(self):
        from lib.config.service import ConfigService

        factory, engine = await _make_factory()
        try:
            async with factory() as session:
                await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
                await session.commit()
            resolver = ConfigResolver(factory)
            assert await resolver.default_audio_backend() == ("dashscope", "qwen3-tts-flash")
        finally:
            await engine.dispose()

    async def test_resolve_audio_backend_payload_short_circuit(self):
        factory, engine = await _make_factory()
        try:
            resolver = ConfigResolver(factory)
            result = await resolver.resolve_audio_backend(
                None, {"audio_provider": "dashscope", "audio_model": "qwen3-tts-flash"}
            )
            assert result == ProviderModel("dashscope", "qwen3-tts-flash")
        finally:
            await engine.dispose()


class TestServiceDefaultAudioBackend:
    async def test_falls_back_to_builtin_default(self):
        from lib.config.service import ConfigService

        factory, engine = await _make_factory()
        try:
            async with factory() as session:
                svc = ConfigService(session)
                assert await svc.get_default_audio_backend() == ("dashscope", "qwen3-tts-flash")
        finally:
            await engine.dispose()


class TestServiceNarrationVoice:
    async def test_blank_setting_falls_back_to_default(self):
        # 全局 setting 被保存成空白时回退默认值，与项目级覆盖的 strip 语义一致
        from lib.config.service import ConfigService

        factory, engine = await _make_factory()
        try:
            async with factory() as session:
                svc = ConfigService(session)
                await svc.set_setting("narration_voice", "  ")
                await session.commit()
                assert await svc.get_narration_voice() == "Cherry"

                await svc.set_setting("narration_voice", "Ethan")
                await session.commit()
                assert await svc.get_narration_voice() == "Ethan"
        finally:
            await engine.dispose()
