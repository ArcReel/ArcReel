"""Filesystem-backed sound provenance contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.speech_artifact_provenance import CharacterVoiceEvidence, build_video_speech_basis
from lib.speech_composition import SpeechFieldLocation, SpeechMode, SpeechOwner, SpeechPreparation, SpeechUtterance

pytestmark = pytest.mark.integration


def test_character_video_speech_basis_uses_reference_audio_content_not_path(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "renamed.wav"
    first.write_bytes(b"same voice")
    second.write_bytes(b"same voice")
    preparation = SpeechPreparation(
        unit_id="E1U01",
        mode=SpeechMode.CHARACTER_SPEECH,
        utterances=(
            SpeechUtterance(
                owner=SpeechOwner.CHARACTER,
                speaker="阿离",
                text="快走。",
                location=SpeechFieldLocation(("utterances", 0, "text")),
            ),
        ),
    )

    baseline = build_video_speech_basis(
        preparation,
        voices=(CharacterVoiceEvidence(speaker="阿离", voice_style="清亮", reference_audio=first),),
    )
    renamed = build_video_speech_basis(
        preparation,
        voices=(CharacterVoiceEvidence(speaker="阿离", voice_style="清亮", reference_audio=second),),
    )
    second.write_bytes(b"different voice")
    changed = build_video_speech_basis(
        preparation,
        voices=(CharacterVoiceEvidence(speaker="阿离", voice_style="清亮", reference_audio=second),),
    )

    assert renamed == baseline
    assert changed != baseline
