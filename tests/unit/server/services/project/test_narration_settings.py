"""新建项目的旁白配置：TTS 项目缺省字段以全局默认预填，写入项目后成为快照。"""

from __future__ import annotations

import pytest

from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.speech.narration_config import NarrationConfigError
from server.services.project.narration_settings import NarrationSettingsInput, new_project_narration_fields
from server.services.tasks import generation_context


async def _set_globals(db_factory, **settings: str) -> None:
    async with db_factory() as session:
        svc = ConfigService(session)
        for key, value in settings.items():
            await svc.set_setting(key, value)
        await session.commit()


async def _set_api_keys(db_factory, *providers: str) -> None:
    async with db_factory() as session:
        svc = ConfigService(session)
        for provider in providers:
            await svc.set_provider_config(provider, "api_key", f"test-{provider}-key")
        await session.commit()


@pytest.fixture(autouse=True)
def _clear_backend_cache():
    """音色表经 backend 缓存读取，缓存是模块级进程内状态。"""
    generation_context.invalidate_backend_cache()
    yield
    generation_context.invalidate_backend_cache()


async def test_post_production_writes_only_delivery(db_factory):
    fields = await new_project_narration_fields(
        NarrationSettingsInput(delivery="post_production"), resolver=ConfigResolver(db_factory)
    )
    assert fields == {"narration_delivery": "post_production"}


async def test_tts_project_prefills_every_omitted_field_from_global_defaults(db_factory):
    await _set_globals(
        db_factory,
        default_audio_backend="dashscope/qwen3-tts-flash",
        narration_voice="Ethan",
        narration_speed="1.2",
    )
    fields = await new_project_narration_fields(
        NarrationSettingsInput(delivery="use_tts"), resolver=ConfigResolver(db_factory)
    )
    assert fields == {
        "narration_delivery": "use_tts",
        "audio_backend": "dashscope/qwen3-tts-flash",
        "narration_voice": "Ethan",
        "narration_speed": 1.2,
    }


async def test_explicit_values_win_and_explicit_null_speed_is_kept(db_factory):
    await _set_globals(db_factory, narration_speed="1.2")
    fields = await new_project_narration_fields(
        NarrationSettingsInput(
            delivery="use_tts",
            audio_backend="custom-3/tts-1",
            narration_voice=" alloy ",
            narration_speed=None,
            provided=frozenset({"audio_backend", "narration_voice", "narration_speed"}),
        ),
        resolver=ConfigResolver(db_factory),
    )
    assert fields == {"narration_delivery": "use_tts", "audio_backend": "custom-3/tts-1", "narration_voice": "alloy"}


@pytest.mark.parametrize("backend", ["dashscope", "", "unknown-vendor/model"])
async def test_tts_project_rejects_incomplete_or_unknown_model(db_factory, backend):
    with pytest.raises(NarrationConfigError) as caught:
        await new_project_narration_fields(
            NarrationSettingsInput(
                delivery="use_tts",
                audio_backend=backend,
                provided=frozenset({"audio_backend"}),
            ),
            resolver=ConfigResolver(db_factory),
        )
    assert caught.value.code in {"narration_tts_model_required", "narration_tts_model_invalid"}


async def test_tts_project_rejects_non_audio_registry_model(db_factory):
    with pytest.raises(NarrationConfigError) as caught:
        await new_project_narration_fields(
            NarrationSettingsInput(
                delivery="use_tts",
                audio_backend="dashscope/wan3.0-video",
                provided=frozenset({"audio_backend"}),
            ),
            resolver=ConfigResolver(db_factory),
        )
    assert caught.value.code == "narration_tts_model_invalid"


async def test_non_positive_speed_is_rejected(db_factory):
    with pytest.raises(NarrationConfigError) as caught:
        await new_project_narration_fields(
            NarrationSettingsInput(
                delivery="use_tts",
                audio_backend="dashscope/qwen3-tts-flash",
                narration_speed=0,
                provided=frozenset({"audio_backend", "narration_speed"}),
            ),
            resolver=ConfigResolver(db_factory),
        )
    assert caught.value.code == "narration_tts_speed_invalid"


async def test_tts_project_prefills_a_voice_the_default_model_supports(db_factory):
    """全局音色不在默认内置模型的音色表中（缺省 Cherry 只属于 DashScope）：预填表中第一个音色。"""
    await _set_api_keys(db_factory, "gemini-aistudio")
    await _set_globals(db_factory, default_audio_backend="gemini-aistudio/gemini-3.8-flash-tts")
    fields = await new_project_narration_fields(
        NarrationSettingsInput(delivery="use_tts"), resolver=ConfigResolver(db_factory)
    )
    assert fields["audio_backend"] == "gemini-aistudio/gemini-3.8-flash-tts"
    assert fields["narration_voice"] == "Zephyr"


async def test_omitted_voice_follows_the_explicitly_chosen_model(db_factory):
    """显式选了 DashScope、全局默认却是 Gemini：音色按实际写入的模型校正，Cherry 保留。"""
    await _set_api_keys(db_factory, "gemini-aistudio", "dashscope")
    await _set_globals(db_factory, default_audio_backend="gemini-aistudio/gemini-3.8-flash-tts")
    fields = await new_project_narration_fields(
        NarrationSettingsInput(
            delivery="use_tts",
            audio_backend="dashscope/qwen3-tts-flash",
            provided=frozenset({"audio_backend"}),
        ),
        resolver=ConfigResolver(db_factory),
    )
    assert fields["audio_backend"] == "dashscope/qwen3-tts-flash"
    assert fields["narration_voice"] == "Cherry"


async def test_voice_is_kept_when_the_model_cannot_be_constructed(db_factory, monkeypatch):
    """取不到音色表（SDK 构造失败）时不阻断建项，全局音色原样预填。"""

    def _broken_client(**_kwargs):
        raise OSError("credentials unreadable")

    monkeypatch.setattr("google.genai.Client", _broken_client)
    await _set_api_keys(db_factory, "gemini-aistudio")
    await _set_globals(db_factory, default_audio_backend="gemini-aistudio/gemini-3.8-flash-tts")
    fields = await new_project_narration_fields(
        NarrationSettingsInput(delivery="use_tts"), resolver=ConfigResolver(db_factory)
    )
    assert fields["narration_voice"] == "Cherry"


async def test_custom_provider_default_keeps_the_global_voice(db_factory):
    """自定义供应商的音色表不一定完整：不按音色表改写全局音色。"""
    await _set_globals(db_factory, default_audio_backend="custom-3/relay-tts", narration_voice="relay-voice")
    fields = await new_project_narration_fields(
        NarrationSettingsInput(delivery="use_tts"), resolver=ConfigResolver(db_factory)
    )
    assert fields["narration_voice"] == "relay-voice"
