"""Sound-owned Artifact Manifest provenance contracts."""

from __future__ import annotations

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    InMemoryArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.narration_delivery import POST_PRODUCTION, USE_TTS, TtsSynthesisSettings, build_narration_audio_basis
from lib.speech_artifact_provenance import (
    CharacterVoiceEvidence,
    SelectedMediaEvidence,
    build_mechanical_subtitle_basis,
    build_presentation_basis,
    build_video_duration_basis,
    build_video_speech_basis,
)
from lib.speech_composition import SpeechFieldLocation, SpeechMode, SpeechOwner, SpeechPreparation, SpeechUtterance


def _preparation(
    mode: SpeechMode,
    *utterances: tuple[str | None, str],
    unit_id: str = "E1U01",
) -> SpeechPreparation:
    owner = SpeechOwner.CHARACTER if mode is SpeechMode.CHARACTER_SPEECH else SpeechOwner.NARRATOR
    return SpeechPreparation(
        unit_id=unit_id,
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


def _character_speech(*utterances: tuple[str | None, str]) -> SpeechPreparation:
    return _preparation(SpeechMode.CHARACTER_SPEECH, *(utterances or (("阿离", "快走。"),)))


def _narration(*texts: str) -> SpeechPreparation:
    return _preparation(SpeechMode.NARRATOR_VOICEOVER, *((None, text) for text in (texts or ("旁白正文",))))


def _basis(kind: str, value: object) -> ArtifactBasis:
    return ArtifactBasis.build(kind, kind_version=1, inputs={"value": value})


def _media(kind: str, value: object, *, content: str, duration: float) -> SelectedMediaEvidence:
    return SelectedMediaEvidence(
        basis=ArtifactBasisDescriptor.from_basis(_basis(kind, value)),
        content_digest=f"sha256-v1:{content * 64}",
        actual_duration_seconds=duration,
    )


def test_character_video_speech_basis_tracks_ordered_text_speaker_and_used_voice_style() -> None:
    preparation = _character_speech(("A\u0301nh", " 第一行\r\n第二行 "), ("阿离", "快走。"))
    profiles = (
        CharacterVoiceEvidence(speaker="Ánh", voice_style=" 低沉 "),
        CharacterVoiceEvidence(speaker="阿离", voice_style="清亮"),
    )
    baseline = build_video_speech_basis(preparation, voices=profiles)

    assert baseline == build_video_speech_basis(
        _character_speech(("Ánh", "第一行\n第二行"), ("阿离", "快走。")),
        voices=profiles,
    )
    assert baseline != build_video_speech_basis(
        _character_speech(("Ánh", "第一行\n第二行"), ("阿离", "别走。")),
        voices=profiles,
    )
    assert baseline != build_video_speech_basis(
        _character_speech(("Ánh", "第一行\n第二行"), ("阿宁", "快走。")),
        voices=profiles,
    )
    assert baseline != build_video_speech_basis(
        preparation,
        voices=(profiles[0], CharacterVoiceEvidence(speaker="阿离", voice_style="沙哑")),
    )


def test_character_video_speech_basis_ignores_unreferenced_character_voice() -> None:
    preparation = _character_speech(("阿离", "快走。"))
    baseline = build_video_speech_basis(
        preparation,
        voices=(
            CharacterVoiceEvidence(speaker="阿离", voice_style="清亮"),
            CharacterVoiceEvidence(speaker="路人", voice_style="低沉"),
        ),
    )

    changed_unrelated = build_video_speech_basis(
        preparation,
        voices=(
            CharacterVoiceEvidence(speaker="阿离", voice_style="清亮"),
            CharacterVoiceEvidence(speaker="路人", voice_style="尖细"),
        ),
    )

    assert changed_unrelated == baseline


def test_narrator_text_does_not_enter_video_speech_or_same_tier_duration_basis() -> None:
    visual = _basis("test/visual", "v1")
    before = compose_video_artifact_basis(
        visual=visual,
        speech=build_video_speech_basis(_narration("原旁白")),
        duration=build_video_duration_basis(8),
    )
    after_same_tier = compose_video_artifact_basis(
        visual=visual,
        speech=build_video_speech_basis(_narration("修改后的旁白")),
        duration=build_video_duration_basis(8),
    )
    after_cross_tier = compose_video_artifact_basis(
        visual=visual,
        speech=build_video_speech_basis(_narration("修改后的旁白")),
        duration=build_video_duration_basis(12),
    )

    assert after_same_tier == before
    assert after_cross_tier != before


def test_video_component_composition_accepts_frozen_descriptors() -> None:
    visual = _basis("test/visual", "v1")
    speech = build_video_speech_basis(_character_speech())
    duration = build_video_duration_basis(8)

    direct = compose_video_artifact_basis(visual=visual, speech=speech, duration=duration)
    frozen = compose_video_artifact_basis(
        visual=ArtifactBasisDescriptor.from_basis(visual),
        speech=ArtifactBasisDescriptor.from_basis(speech),
        duration=ArtifactBasisDescriptor.from_basis(duration),
    )

    assert frozen == direct


def test_mechanical_subtitle_uses_selected_media_boundary_and_tts_basis() -> None:
    video = _media("test/video", "v1", content="a", duration=8.0)
    tts = _media("test/tts", "voice-1", content="b", duration=6.0)
    narration = _narration("第一句", "第二句")

    use_tts = build_mechanical_subtitle_basis(
        narration,
        variant=USE_TTS,
        video=video,
        narration_audio=tts,
    )
    post = build_mechanical_subtitle_basis(
        narration,
        variant=POST_PRODUCTION,
        video=video,
    )

    assert use_tts != build_mechanical_subtitle_basis(
        _narration("第一句改", "第二句"),
        variant=USE_TTS,
        video=video,
        narration_audio=tts,
    )
    assert use_tts != build_mechanical_subtitle_basis(
        narration,
        variant=USE_TTS,
        video=video,
        narration_audio=_media("test/tts", "voice-2", content="c", duration=6.0),
    )
    assert post != use_tts


def test_presentation_variants_are_independent_manifest_entries() -> None:
    video = _media("test/video", "v1", content="a", duration=8.0)
    tts = _media("test/tts", "voice-1", content="b", duration=6.0)
    narration = _narration("旁白")
    post_subtitle = build_mechanical_subtitle_basis(
        narration,
        variant=POST_PRODUCTION,
        video=video,
    )
    tts_subtitle = build_mechanical_subtitle_basis(
        narration,
        variant=USE_TTS,
        video=video,
        narration_audio=tts,
    )
    post = build_presentation_basis(
        variant=POST_PRODUCTION,
        video=video,
        subtitle=post_subtitle,
    )
    use_tts = build_presentation_basis(
        variant=USE_TTS,
        video=video,
        subtitle=tts_subtitle,
        narration_audio=tts,
    )
    adapter = InMemoryArtifactManifestAdapter(
        artifacts={"presentations/E1U01-post.json", "presentations/E1U01-tts.json"}
    )
    manifest = ArtifactManifest(adapter)
    post_key = ArtifactKey.episode_presentation(1, "E1U01", POST_PRODUCTION)
    tts_key = ArtifactKey.episode_presentation(1, "E1U01", USE_TTS)
    manifest.register(post_key, artifact_path="presentations/E1U01-post.json", basis=post)
    manifest.register(tts_key, artifact_path="presentations/E1U01-tts.json", basis=use_tts)

    changed_tts = _media("test/tts", "voice-2", content="c", duration=6.0)
    changed_tts_subtitle = build_mechanical_subtitle_basis(
        narration,
        variant=USE_TTS,
        video=video,
        narration_audio=changed_tts,
    )
    changed_tts_presentation = build_presentation_basis(
        variant=USE_TTS,
        video=video,
        subtitle=changed_tts_subtitle,
        narration_audio=changed_tts,
    )

    assert (
        manifest.compare(post_key, artifact_path="presentations/E1U01-post.json", basis=post).status
        is ArtifactStatus.CURRENT
    )
    assert (
        manifest.compare(
            tts_key,
            artifact_path="presentations/E1U01-tts.json",
            basis=changed_tts_presentation,
        ).status
        is ArtifactStatus.STALE
    )


def test_stale_comparison_is_reversible_and_preserves_paid_media() -> None:
    path = "videos/unit_E1U01.mp4"
    adapter = InMemoryArtifactManifestAdapter(artifacts={path})
    manifest = ArtifactManifest(adapter)
    key = ArtifactKey.episode_video(1, "E1U01")
    visual = _basis("test/visual", "v1")
    original = compose_video_artifact_basis(
        visual=visual,
        speech=build_video_speech_basis(_character_speech(("阿离", "快走。"))),
        duration=build_video_duration_basis(8),
    )
    changed = compose_video_artifact_basis(
        visual=visual,
        speech=build_video_speech_basis(_character_speech(("阿离", "别走。"))),
        duration=build_video_duration_basis(8),
    )
    manifest.register(key, artifact_path=path, basis=original)

    assert manifest.compare(key, artifact_path=path, basis=changed).status is ArtifactStatus.STALE
    assert manifest.compare(key, artifact_path=path, basis=original).status is ArtifactStatus.CURRENT
    assert adapter.inspect_artifact(path).present


def test_tts_change_stales_tts_subtitle_and_tts_presentation_but_not_video_or_post_variant() -> None:
    narration = _narration("旁白")
    settings = TtsSynthesisSettings("provider", "model", "voice", 1.0)
    changed_settings = TtsSynthesisSettings("provider", "model", "other-voice", 1.0)
    tts_basis = build_narration_audio_basis(narration, settings)
    changed_tts_basis = build_narration_audio_basis(narration, changed_settings)
    video_basis = compose_video_artifact_basis(
        visual=_basis("test/visual", "v1"),
        speech=build_video_speech_basis(narration),
        duration=build_video_duration_basis(8),
    )
    video = SelectedMediaEvidence(
        ArtifactBasisDescriptor.from_basis(video_basis),
        f"sha256-v1:{'a' * 64}",
        8.0,
    )
    tts = SelectedMediaEvidence(
        ArtifactBasisDescriptor.from_basis(tts_basis),
        f"sha256-v1:{'b' * 64}",
        6.0,
    )
    changed_tts = SelectedMediaEvidence(
        ArtifactBasisDescriptor.from_basis(changed_tts_basis),
        f"sha256-v1:{'b' * 64}",
        6.0,
    )
    post_subtitle = build_mechanical_subtitle_basis(narration, variant=POST_PRODUCTION, video=video)
    tts_subtitle = build_mechanical_subtitle_basis(narration, variant=USE_TTS, video=video, narration_audio=tts)

    assert video_basis == compose_video_artifact_basis(
        visual=_basis("test/visual", "v1"),
        speech=build_video_speech_basis(narration),
        duration=build_video_duration_basis(8),
    )
    assert post_subtitle == build_mechanical_subtitle_basis(
        narration,
        variant=POST_PRODUCTION,
        video=video,
    )
    assert tts_subtitle != build_mechanical_subtitle_basis(
        narration,
        variant=USE_TTS,
        video=video,
        narration_audio=changed_tts,
    )
    assert build_presentation_basis(
        variant=POST_PRODUCTION,
        video=video,
        subtitle=post_subtitle,
    ) == build_presentation_basis(
        variant=POST_PRODUCTION,
        video=video,
        subtitle=post_subtitle,
    )


@pytest.mark.parametrize("invalid", [0, -1, True, 8.5, "8"])
def test_video_duration_basis_requires_a_request_tier(invalid: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        build_video_duration_basis(invalid)  # type: ignore[arg-type]


def test_use_tts_subtitle_and_presentation_require_narration_audio() -> None:
    narration = _narration("旁白")
    video = _media("test/video", "v1", content="a", duration=8.0)

    with pytest.raises(ValueError, match="narration audio"):
        build_mechanical_subtitle_basis(narration, variant=USE_TTS, video=video)
    with pytest.raises(ValueError, match="narration audio"):
        build_presentation_basis(
            variant=USE_TTS,
            video=video,
            subtitle=_basis("test/subtitle", "v1"),
        )
