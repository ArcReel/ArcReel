"""新建项目的旁白配置：TTS 项目缺省字段以全局默认预填，写入项目后成为快照。"""

from __future__ import annotations

import pytest

from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.speech.narration_config import NarrationConfigError
from server.services.project.narration_settings import NarrationSettingsInput, new_project_narration_fields


async def _set_globals(db_factory, **settings: str) -> None:
    async with db_factory() as session:
        svc = ConfigService(session)
        for key, value in settings.items():
            await svc.set_setting(key, value)
        await session.commit()


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
