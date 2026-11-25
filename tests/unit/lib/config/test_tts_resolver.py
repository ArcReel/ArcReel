"""音频 provider 解析：AudioRuntime（payload > project > 全局默认 / auto）与新建 TTS 项目的预填值。"""

from __future__ import annotations

import pytest

from lib.config.recommended_models import FROZEN_SETTING_KEY
from lib.config.resolver import ConfigResolver, NoAvailableProviderError, ProviderModel
from lib.config.service import ConfigService
from lib.video_runtime.audio import AudioRuntime, default_narration_tts


class TestResolveAudioProviderModel:
    async def test_payload_wins(self, db_factory):
        result = await AudioRuntime(ConfigResolver(db_factory)).read_identity(
            {"audio_backend": "dashscope/qwen3-tts-flash"},
            {"audio_provider": "dashscope", "audio_model": "qwen3-tts-flash"},
        )
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")

    async def test_payload_untrusted_provider_ignored(self, db_factory):
        result = await AudioRuntime(ConfigResolver(db_factory)).read_identity(
            {"audio_backend": "dashscope/qwen3-tts-flash"},
            {"audio_provider": "totally-unknown", "audio_model": "x"},
        )
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")

    async def test_project_override(self, db_factory):
        result = await AudioRuntime(ConfigResolver(db_factory)).read_identity(
            {"audio_backend": "dashscope/qwen3-tts-flash"},
            None,
        )
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")

    async def test_falls_back_to_global_setting(self, db_factory):
        async with db_factory() as session:
            await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await session.commit()
        result = await AudioRuntime(ConfigResolver(db_factory)).read_identity(None, None)
        assert result == ProviderModel("dashscope", "qwen3-tts-flash")

    async def test_unset_settings_do_not_pick_a_catalog_recommendation(self, db_factory):
        async with db_factory() as session:
            await ConfigService(session).set_setting(FROZEN_SETTING_KEY, "[]")
            await session.commit()
        with pytest.raises(NoAvailableProviderError):
            await AudioRuntime(ConfigResolver(db_factory)).read_identity(None, None)


class TestDefaultNarrationTts:
    """全局音频默认、音色与语速只作为新建 TTS 项目的预填值。"""

    async def test_reads_global_settings(self, db_factory):
        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await svc.set_setting("narration_voice", "Ethan")
            await svc.set_setting("narration_speed", "1.2")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        assert await default_narration_tts(resolver) == (
            ProviderModel("dashscope", "qwen3-tts-flash"),
            "Ethan",
            1.2,
        )

    async def test_unset_voice_and_speed_use_service_defaults(self, db_factory):
        async with db_factory() as session:
            await ConfigService(session).set_setting("default_audio_backend", "dashscope/qwen3-tts-flash")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        _backend, voice, speed = await default_narration_tts(resolver)
        assert voice == ""
        assert speed is None


class TestServiceDefaultAudioBackend:
    async def test_falls_back_to_builtin_default(self, db_factory):
        async with db_factory() as session:
            svc = ConfigService(session)
            assert await svc.get_default_audio_backend() == ("dashscope", "qwen3-tts-flash")


class TestServiceNarrationVoice:
    async def test_blank_setting_has_no_voice(self, db_factory):
        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_setting("narration_voice", "  ")
            await session.commit()
            assert await svc.get_narration_voice() == ""

            await svc.set_setting("narration_voice", "Ethan")
            await session.commit()
            assert await svc.get_narration_voice() == "Ethan"
