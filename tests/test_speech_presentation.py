"""Unified presentation read-model contracts."""

from __future__ import annotations

import pytest

from lib.artifact_manifest import ArtifactBasis, ArtifactBasisDescriptor
from lib.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.speech_artifact_provenance import SelectedMediaEvidence
from lib.speech_composition import SpeechFieldLocation, SpeechMode, SpeechOwner, SpeechPreparation, SpeechUtterance
from lib.speech_presentation import (
    MechanicalSubtitleTiming,
    PresentationBoundaryError,
    PresentationMedia,
    materialize_speech_presentation,
)

pytestmark = pytest.mark.unit


def _basis(kind: str, value: str) -> ArtifactBasisDescriptor:
    return ArtifactBasisDescriptor.from_basis(ArtifactBasis.build(kind, kind_version=1, inputs={"value": value}))


def _media(
    path: str,
    *,
    kind: str,
    duration: float,
    version: int = 1,
    selection: str = "current",
    currency: str = "current",
) -> PresentationMedia:
    digest_char = "a" if kind == "v" else "b"
    return PresentationMedia(
        artifact_path=path,
        version=version,
        selection=selection,
        currency=currency,
        evidence=SelectedMediaEvidence(
            basis=_basis(kind, str(version)),
            content_digest=f"sha256-v1:{digest_char * 64}",
            actual_duration_seconds=duration,
        ),
    )


def _speech(mode: SpeechMode, *utterances: tuple[str | None, str]) -> SpeechPreparation:
    owner = SpeechOwner.CHARACTER if mode is SpeechMode.CHARACTER_SPEECH else SpeechOwner.NARRATOR
    return SpeechPreparation(
        unit_id="E1U01",
        mode=mode,
        utterances=tuple(
            SpeechUtterance(
                owner=owner,
                speaker=speaker,
                text=text,
                location=SpeechFieldLocation(("utterances", index, "text")),
            )
            for index, (speaker, text) in enumerate(utterances)
        ),
    )


def test_character_post_uses_actual_video_boundary_and_unity_provider_audio() -> None:
    presentation = materialize_speech_presentation(
        _speech(
            SpeechMode.CHARACTER_SPEECH,
            ("阿离", "甲"),
            ("阿离", "乙乙乙"),
        ),
        variant=POST_PRODUCTION,
        video=_media("versions/videos/E1U01_v1.mp4", kind="v", duration=6.4),
        provider_audio_enabled=True,
    )

    assert presentation.video.duration_microseconds == 6_400_000
    assert presentation.video.gain == 1.0
    assert presentation.video.audio_enabled is True
    assert presentation.narration_audio is None
    assert [(cue.start_microseconds, cue.duration_microseconds, cue.text) for cue in presentation.subtitles] == [
        (0, 1_600_000, "甲"),
        (1_600_000, 4_800_000, "乙乙乙"),
    ]
    assert all(cue.owner is SpeechOwner.CHARACTER for cue in presentation.subtitles)
    artifact_cues = presentation.subtitle_artifact_dict()["cues"]
    assert isinstance(artifact_cues, list)
    first_artifact_cue = artifact_cues[0]
    assert isinstance(first_artifact_cue, dict)
    assert first_artifact_cue["owner"] == "character"
    assert "00:00:01.600 --> 00:00:06.400" in presentation.subtitles_webvtt()
    assert presentation.timing == "mechanical"
    assert presentation.subtitles_adjustable is True


def test_explicit_provider_audio_off_preserves_file_but_disables_its_presentation_track() -> None:
    video = _media("versions/videos/E1U01_v1.mp4", kind="v", duration=4.0)
    presentation = materialize_speech_presentation(
        _speech(SpeechMode.SILENT),
        variant=POST_PRODUCTION,
        video=video,
        provider_audio_enabled=False,
    )

    assert presentation.video.media is video
    assert presentation.video.audio_enabled is False
    assert presentation.video.gain == 0.0
    assert presentation.subtitles == ()


def test_narrator_use_tts_overlays_both_tracks_at_unity_and_times_subtitles_to_tts() -> None:
    presentation = materialize_speech_presentation(
        _speech(
            SpeechMode.NARRATOR_VOICEOVER,
            (None, "A\u0301"),
            (None, "山河"),
        ),
        variant=USE_TTS,
        video=_media("versions/videos/E1U01_v1.mp4", kind="v", duration=8.0),
        narration_audio=_media("versions/audio/E1U01_v2.wav", kind="a", duration=6.0, version=2),
        provider_audio_enabled=True,
    )

    assert presentation.video.gain == 1.0
    assert presentation.narration_audio is not None
    assert presentation.narration_audio.gain == 1.0
    assert presentation.narration_audio.start_microseconds == 0
    assert presentation.narration_audio.duration_microseconds == 6_000_000
    # NFC turns A + combining acute into one code point, so weights are 1:2.
    assert [(cue.start_microseconds, cue.duration_microseconds, cue.text) for cue in presentation.subtitles] == [
        (0, 2_000_000, "Á"),
        (2_000_000, 4_000_000, "山河"),
    ]


def test_narrator_post_needs_no_tts_and_uses_actual_video_not_planned_duration() -> None:
    presentation = materialize_speech_presentation(
        _speech(SpeechMode.NARRATOR_VOICEOVER, (None, "旁白")),
        variant=POST_PRODUCTION,
        video=_media("versions/videos/E1U01_v1.mp4", kind="v", duration=5.25),
        provider_audio_enabled=True,
    )

    assert presentation.narration_audio is None
    assert [(cue.start_microseconds, cue.duration_microseconds) for cue in presentation.subtitles] == [(0, 5_250_000)]


def test_webvtt_projection_collapses_blank_paragraphs_without_changing_canonical_text() -> None:
    presentation = materialize_speech_presentation(
        _speech(SpeechMode.NARRATOR_VOICEOVER, (None, "第一段\n\n第二段")),
        variant=POST_PRODUCTION,
        video=_media("versions/videos/E1U01_v1.mp4", kind="v", duration=5.0),
        provider_audio_enabled=True,
    )

    assert presentation.subtitles[0].text == "第一段\n\n第二段"
    assert presentation.subtitle_artifact_dict()["cues"][0]["text"] == "第一段\n\n第二段"
    assert presentation.subtitles_webvtt() == ("WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.000\n第一段\n第二段\n")


def test_use_tts_rejects_non_narrator_and_audio_longer_than_video_without_clipping() -> None:
    video = _media("versions/videos/E1U01_v1.mp4", kind="v", duration=5.0)
    audio = _media("versions/audio/E1U01_v1.wav", kind="a", duration=5.1)

    with pytest.raises(ValueError, match="narrator voiceover"):
        materialize_speech_presentation(
            _speech(SpeechMode.CHARACTER_SPEECH, ("阿离", "快走")),
            variant=USE_TTS,
            video=video,
            narration_audio=audio,
            provider_audio_enabled=True,
        )
    with pytest.raises(PresentationBoundaryError, match="exceeds video boundary"):
        materialize_speech_presentation(
            _speech(SpeechMode.NARRATOR_VOICEOVER, (None, "旁白")),
            variant=USE_TTS,
            video=video,
            narration_audio=audio,
            provider_audio_enabled=True,
        )


def test_source_selection_and_currency_are_aggregated_without_hiding_history() -> None:
    presentation = materialize_speech_presentation(
        _speech(SpeechMode.NARRATOR_VOICEOVER, (None, "旁白")),
        variant=USE_TTS,
        video=_media(
            "versions/videos/E1U01_v3.mp4",
            kind="v",
            duration=8.0,
            version=3,
            selection="history",
            currency="stale",
        ),
        narration_audio=_media("versions/audio/E1U01_v2.wav", kind="a", duration=6.0, version=2),
        provider_audio_enabled=True,
    )

    assert presentation.selection == "history"
    assert presentation.currency == "stale"
    assert presentation.video.media.artifact_path == "versions/videos/E1U01_v3.mp4"


def test_timing_policy_identity_changes_only_derived_bases() -> None:
    preparation = _speech(SpeechMode.NARRATOR_VOICEOVER, (None, "旁白"))
    video = _media("versions/videos/E1U01_v1.mp4", kind="v", duration=8.0)
    baseline = materialize_speech_presentation(
        preparation,
        variant=POST_PRODUCTION,
        video=video,
        provider_audio_enabled=True,
    )
    changed = materialize_speech_presentation(
        preparation,
        variant=POST_PRODUCTION,
        video=video,
        provider_audio_enabled=True,
        timing=MechanicalSubtitleTiming(policy_version=2),
    )

    assert changed.video.media.evidence == baseline.video.media.evidence
    assert changed.subtitle_basis != baseline.subtitle_basis
    assert changed.presentation_basis != baseline.presentation_basis
