from __future__ import annotations

from copy import deepcopy

import pytest

from lib.artifact_manifest import ArtifactBasis, compose_video_artifact_basis
from lib.speech_artifact_provenance import build_video_duration_basis
from lib.video_artifact_facts import VideoArtifactCurrencyFacts

pytestmark = pytest.mark.unit


def _facts() -> VideoArtifactCurrencyFacts:
    visual = ArtifactBasis.build(
        "artifact-visual/video-storyboard",
        kind_version=1,
        inputs={
            "resource_id": "E1S01",
            "visual_prompt": {"action": "run", "camera_motion": "Static"},
            "canvas": {"aspect_ratio": "9:16"},
            "frames": [{"role": "storyboard", "sha256": "a" * 64}],
        },
    )
    speech = ArtifactBasis.build(
        "artifact-speech/video",
        kind_version=1,
        inputs={
            "mode": "character_speech",
            "utterances": [{"speaker": "阿离", "text": "走。"}],
            "voices": [{"speaker": "阿离", "voice_style": "", "reference_audio_digest": None}],
        },
    )
    duration = build_video_duration_basis(8)
    return VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=8,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=("阿离",),
        duration_tiers=(4, 8, 12),
        reference_image_limit=None,
        parent_version=3,
    )


def test_video_artifact_currency_round_trip_preserves_complete_verified_evidence() -> None:
    facts = _facts()

    restored = VideoArtifactCurrencyFacts.from_dict(facts.to_dict())

    assert restored == facts
    assert restored.video_descriptor.digest == facts.video_basis.digest
    assert restored.currency_digest.startswith("sha256-v1:")


def test_video_artifact_currency_rejects_component_input_and_route_knob_tampering() -> None:
    raw = _facts().to_dict()
    changed_visual = deepcopy(raw)
    changed_visual["visual_basis"]["inputs"]["unit"] = "E1S02"  # type: ignore[index]
    with pytest.raises(ValueError, match="self-verifying"):
        VideoArtifactCurrencyFacts.from_dict(changed_visual)

    changed_token = deepcopy(raw)
    changed_token["parent_version"] = 4
    with pytest.raises(ValueError, match="currency digest"):
        VideoArtifactCurrencyFacts.from_dict(changed_token)


def test_video_artifact_currency_rejects_incoherent_components_and_request_tier() -> None:
    facts = _facts()
    other_duration = build_video_duration_basis(12)

    with pytest.raises(ValueError, match="paid request tier"):
        VideoArtifactCurrencyFacts(
            episode=facts.episode,
            request_duration_seconds=8,
            visual_basis=facts.visual_basis,
            speech_basis=facts.speech_basis,
            duration_basis=other_duration,
            video_basis=compose_video_artifact_basis(
                visual=facts.visual_basis,
                speech=facts.speech_basis,
                duration=other_duration,
            ),
            voice_style_speakers=facts.voice_style_speakers,
            duration_tiers=(8, 12),
            reference_image_limit=None,
            parent_version=0,
        )


def test_video_artifact_currency_accepts_unlimited_reference_projection_but_rejects_invalid_limits() -> None:
    facts = _facts()
    reference = ArtifactBasis.build(
        "artifact-visual/video-reference",
        kind_version=1,
        inputs={
            "unit_id": "E1U1",
            "visual_shots": [],
            "style": "",
            "canvas": {"aspect_ratio": "9:16"},
            "request_references": [],
        },
    )
    video = compose_video_artifact_basis(
        visual=reference,
        speech=facts.speech_basis,
        duration=facts.duration_basis,
    )

    unlimited = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=8,
        visual_basis=reference,
        speech_basis=facts.speech_basis,
        duration_basis=facts.duration_basis,
        video_basis=video,
        voice_style_speakers=facts.voice_style_speakers,
        duration_tiers=facts.duration_tiers,
        reference_image_limit=None,
        parent_version=0,
    )
    assert VideoArtifactCurrencyFacts.from_dict(unlimited.to_dict()) == unlimited

    with pytest.raises(ValueError, match="unlimited or non-negative"):
        VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=8,
            visual_basis=reference,
            speech_basis=facts.speech_basis,
            duration_basis=facts.duration_basis,
            video_basis=video,
            voice_style_speakers=facts.voice_style_speakers,
            duration_tiers=facts.duration_tiers,
            reference_image_limit=-1,
            parent_version=0,
        )
