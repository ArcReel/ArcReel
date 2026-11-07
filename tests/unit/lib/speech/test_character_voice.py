"""Character-voice binding values and normalization."""

import pytest

from lib.speech.character_voice import DEFAULT_CHARACTER_VOICE_BINDING, character_voice_binding


def test_no_project_context_returns_none():
    """Without project context (e.g. catalog queries), return None rather than implying explicit user binding selection."""
    assert character_voice_binding(None) is None


def test_declared_value_is_returned():
    assert character_voice_binding({"character_voice_binding": "reference_audio"}) == "reference_audio"
    assert character_voice_binding({"character_voice_binding": "prompt"}) == "prompt"


def test_missing_field_falls_back_to_default():
    assert character_voice_binding({}) == DEFAULT_CHARACTER_VOICE_BINDING


@pytest.mark.parametrize("dirty", [None, "", "REFERENCE_AUDIO", "audio", 1, True, ["prompt"], {"v": "prompt"}])
def test_unreadable_value_falls_back_to_default(dirty):
    """Manual invalid values use default mode: this fallback attaches less reference audio, safer than treating unknown values as reference mode."""
    assert character_voice_binding({"character_voice_binding": dirty}) == DEFAULT_CHARACTER_VOICE_BINDING
