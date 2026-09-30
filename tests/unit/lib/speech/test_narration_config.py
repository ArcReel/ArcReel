"""项目旁白配置：交付方式与 TTS 快照只从 project.json 读，不回落全局默认。"""

from __future__ import annotations

import pytest

from lib.speech.narration_config import (
    NarrationConfigError,
    TtsSynthesisSettings,
    project_narration_delivery,
    project_tts_settings,
    require_project_tts_generation,
    tts_snapshot_fields,
    validate_project_narration_config,
)

_TTS_PROJECT = {
    "narration_delivery": "use_tts",
    "audio_backend": "dashscope/qwen3-tts-flash",
    "narration_voice": "Cherry",
    "narration_speed": 1.2,
}


class TestProjectNarrationDelivery:
    def test_reads_use_tts(self):
        assert project_narration_delivery(_TTS_PROJECT) == "use_tts"

    @pytest.mark.parametrize("raw", [None, "", "tts", 1])
    def test_missing_or_unknown_value_is_post_production(self, raw):
        assert project_narration_delivery({"narration_delivery": raw}) == "post_production"


class TestProjectTtsSettings:
    def test_complete_snapshot(self):
        assert project_tts_settings(_TTS_PROJECT) == TtsSynthesisSettings(
            provider_id="dashscope", model_id="qwen3-tts-flash", voice="Cherry", speed=1.2
        )

    def test_speed_absent_means_provider_default(self):
        project = {key: value for key, value in _TTS_PROJECT.items() if key != "narration_speed"}
        settings = project_tts_settings(project)
        assert settings is not None
        assert settings.speed is None

    def test_integer_speed_reads_as_float(self):
        settings = project_tts_settings({**_TTS_PROJECT, "narration_speed": 2})
        assert settings is not None
        assert settings.speed == 2.0
        assert isinstance(settings.speed, float)

    def test_numeric_string_speed_is_accepted(self):
        settings = project_tts_settings({**_TTS_PROJECT, "narration_speed": " 0.8 "})
        assert settings is not None
        assert settings.speed == 0.8

    def test_snapshot_is_independent_of_delivery(self):
        # 改为后期配音后快照仍在，已有旁白配音的依据照旧可复算
        settings = project_tts_settings({**_TTS_PROJECT, "narration_delivery": "post_production"})
        assert settings is not None
        assert settings.voice == "Cherry"

    @pytest.mark.parametrize(
        "patch",
        [
            {"audio_backend": None},
            {"audio_backend": "dashscope"},
            {"audio_backend": "dashscope/"},
            {"narration_voice": None},
            {"narration_voice": "  "},
            {"narration_speed": 0},
            {"narration_speed": "fast"},
            {"narration_speed": True},
            {"narration_speed": float("inf")},
            {"narration_speed": 10**400},
        ],
    )
    def test_incomplete_or_broken_snapshot_is_none(self, patch):
        project = {**_TTS_PROJECT, **patch}
        project = {key: value for key, value in project.items() if value is not None}
        assert project_tts_settings(project) is None


class TestValidateProjectNarrationConfig:
    def test_post_production_needs_no_snapshot(self):
        assert validate_project_narration_config({"narration_delivery": "post_production"}) is None

    def test_use_tts_with_complete_snapshot(self):
        assert validate_project_narration_config(_TTS_PROJECT) is None

    def test_use_tts_requires_model(self):
        with pytest.raises(NarrationConfigError) as caught:
            validate_project_narration_config({"narration_delivery": "use_tts", "narration_voice": "Cherry"})
        assert caught.value.code == "narration_tts_model_required"

    def test_use_tts_requires_voice(self):
        with pytest.raises(NarrationConfigError) as caught:
            validate_project_narration_config({"narration_delivery": "use_tts", "audio_backend": "dashscope/m"})
        assert caught.value.code == "narration_tts_voice_required"


class TestRequireProjectTtsGeneration:
    def test_tts_project_returns_snapshot(self):
        assert require_project_tts_generation(_TTS_PROJECT).model_id == "qwen3-tts-flash"

    def test_post_production_project_is_rejected_even_with_snapshot(self):
        with pytest.raises(NarrationConfigError) as caught:
            require_project_tts_generation({**_TTS_PROJECT, "narration_delivery": "post_production"})
        assert caught.value.code == "narration_delivery_post_production"

    def test_tts_project_without_snapshot_is_rejected(self):
        with pytest.raises(NarrationConfigError) as caught:
            require_project_tts_generation({"narration_delivery": "use_tts"})
        assert caught.value.code == "narration_tts_model_required"


def test_snapshot_fields_round_trip():
    settings = TtsSynthesisSettings(provider_id="custom-3", model_id="tts-1", voice="alloy", speed=None)
    fields = tts_snapshot_fields(settings)
    assert fields == {"audio_backend": "custom-3/tts-1", "narration_voice": "alloy", "narration_speed": None}
    stored = {"narration_delivery": "use_tts", **{key: value for key, value in fields.items() if value is not None}}
    assert project_tts_settings(stored) == settings
