"""Narration-audio basis and TTS artifact registration contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.speech.narration_delivery import (
    build_narration_audio_basis,
    build_narration_audio_basis_from_canonical_text,
    canonical_narration_text,
    register_narration_audio_transactionally,
)
from lib.speech.speech_composition import (
    SpeechFieldLocation,
    SpeechMode,
    SpeechOwner,
    SpeechPreparation,
    SpeechUtterance,
)


def _narrator_preparation(unit_id: str = "E1U1", *texts: str) -> SpeechPreparation:
    values = texts or ("旁白正文",)
    return SpeechPreparation(
        unit_id=unit_id,
        mode=SpeechMode.NARRATOR_VOICEOVER,
        utterances=tuple(
            SpeechUtterance(
                owner=SpeechOwner.NARRATOR,
                text=text,
                speaker=None,
                location=SpeechFieldLocation(("utterances", index, "text")),
            )
            for index, text in enumerate(values)
        ),
    )


def test_canonical_text_is_nfc_line_normalized_and_ordered() -> None:
    preparation = _narrator_preparation("E1U1", "  Cafe\u0301\r\n第一句  ", "\n第二句\r")

    assert canonical_narration_text(preparation) == "Café\n第一句\n第二句"


def test_tts_basis_changes_with_narration_text() -> None:
    preparation = _narrator_preparation("E1U1", "正文")
    base = build_narration_audio_basis(preparation)

    assert base != build_narration_audio_basis(_narrator_preparation("E1U1", "正文改"))


def test_tts_basis_raw_facts_builder_matches_preparation_builder() -> None:
    preparation = _narrator_preparation("E1U1", "正文")

    assert build_narration_audio_basis(preparation) == build_narration_audio_basis_from_canonical_text("正文")


def test_registration_failure_restores_the_previous_current_basis(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "audio" / "segment_E1U1.wav"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"formal-audio")
    old_preparation = _narrator_preparation("E1U1", "旧旁白")
    new_preparation = _narrator_preparation("E1U1", "新旁白")
    old_basis = register_narration_audio_transactionally(
        project_path=tmp_path,
        episode=1,
        preparation=old_preparation,
    )
    original_put = ProjectArtifactManifestAdapter.put_entry
    calls = 0

    def _write_then_fail(self, key, entry):
        nonlocal calls
        calls += 1
        changed = original_put(self, key, entry)
        if calls == 1:
            raise RuntimeError("manifest finalize failed")
        return changed

    monkeypatch.setattr(ProjectArtifactManifestAdapter, "put_entry", _write_then_fail)

    with pytest.raises(RuntimeError, match="manifest finalize failed"):
        register_narration_audio_transactionally(
            project_path=tmp_path,
            episode=1,
            preparation=new_preparation,
        )

    comparison = ArtifactManifest(ProjectArtifactManifestAdapter(tmp_path)).compare(
        ArtifactKey.episode_audio(1, "E1U1"),
        artifact_path="audio/segment_E1U1.wav",
        basis=old_basis,
    )
    assert comparison.status is ArtifactStatus.CURRENT
