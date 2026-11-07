"""Rendering voice-input settings value object: capability-dict values and silence criteria."""

from __future__ import annotations

import pytest

from lib.script.reference_video.voice_settings import VoiceRenderSettings


def test_from_caps_reads_every_field_from_capability_dict():
    """Only from_caps maps capability-dict keys to fields; missing one makes a call site
    (preview-resolution route / SDK split tool) disagree with execution about voice behavior."""
    settings = VoiceRenderSettings.from_caps(
        {
            "voice_consistency": "native",
            "requested_generate_audio": False,
            "max_reference_audio_count": 3,
            "provider_id": "ark",
            "model": "doubao-seedance-2-0",
            "reference_audio_per_image": True,
        },
        audio_ready={"张三"},
    )
    assert settings == VoiceRenderSettings(
        voice_consistency="native",
        requested_generate_audio=False,
        max_reference_audio=3,
        provider_id="ark",
        model_id="doubao-seedance-2-0",
        audio_ready={"张三"},
        requires_reference_image=True,
    )


def test_from_caps_degrades_to_soft_on_unresolvable_capabilities():
    """On capability resolution failure, callers pass empty dicts: fall back to soft / no reference audio, emitting fewer notices without blocking rendering."""
    settings = VoiceRenderSettings.from_caps({})
    assert settings == VoiceRenderSettings()
    assert settings.voice_consistency == "soft"
    assert settings.requested_generate_audio is True
    assert settings.audio_ready is None


@pytest.mark.parametrize(
    ("voice_consistency", "requested_generate_audio", "expected"),
    [
        ("none", True, True),  # Model produces no audio.
        ("native", False, True),  # Episode disables audio.
        ("soft", False, True),  # Soft constraints also follow the episode audio switch.
        ("none", False, True),  # Both paths combined.
        ("native", True, False),
        ("soft", True, False),
    ],
)
def test_is_silent_covers_both_silent_paths(voice_consistency: str, requested_generate_audio: bool, expected: bool):
    settings = VoiceRenderSettings(
        voice_consistency=voice_consistency,
        requested_generate_audio=requested_generate_audio,
    )
    assert settings.is_silent is expected
