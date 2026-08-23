"""Tests for build_storyboard_video_visual_basis."""

from pathlib import Path

from lib.video_visual_provenance import build_storyboard_video_visual_basis


def test_storyboard_visual_basis_tracks_effective_request_context(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.png"
    storyboard.write_bytes(b"png")
    common = {
        "prompt": {"action": "跑"},
        "storyboard_image": storyboard,
        "end_frame_image": None,
        "content_mode": "narration",
        "utterances": None,
        "voice_characters": None,
        "provider_id": "ark",
        "model_id": "seedance",
        "resolution": "720p",
        "seed": 7,
        "requested_generate_audio": True,
        "has_utterances": False,
    }

    portrait = build_storyboard_video_visual_basis(**common, aspect_ratio="9:16")
    landscape = build_storyboard_video_visual_basis(**common, aspect_ratio="16:9")
    normalized_equivalent = build_storyboard_video_visual_basis(
        **{
            **common,
            "prompt": {
                "action": " 跑 ",
                "camera_motion": "Static",
                "ambiance_audio": "",
                "dialogue": [],
                "ignored": "not sent to the provider",
            },
        },
        aspect_ratio="9:16",
    )

    assert portrait.digest != landscape.digest
    assert portrait.digest == normalized_equivalent.digest

    other_provider = build_storyboard_video_visual_basis(
        **{**common, "provider_id": "openai"},
        aspect_ratio="9:16",
    )
    other_model = build_storyboard_video_visual_basis(
        **{**common, "model_id": "seedance-pro"},
        aspect_ratio="9:16",
    )
    other_resolution = build_storyboard_video_visual_basis(
        **{**common, "resolution": "1080p"},
        aspect_ratio="9:16",
    )
    other_seed = build_storyboard_video_visual_basis(
        **{**common, "seed": 8},
        aspect_ratio="9:16",
    )
    other_audio_request = build_storyboard_video_visual_basis(
        **{**common, "requested_generate_audio": False},
        aspect_ratio="9:16",
    )

    assert portrait.digest != other_provider.digest
    assert portrait.digest != other_model.digest
    assert portrait.digest != other_resolution.digest
    assert portrait.digest != other_seed.digest
    assert portrait.digest != other_audio_request.digest


def test_storyboard_visual_basis_tracks_only_referenced_character_voices(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.png"
    storyboard.write_bytes(b"png")
    common = {
        "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
        "storyboard_image": storyboard,
        "end_frame_image": None,
        "aspect_ratio": "9:16",
        "provider_id": "ark",
        "model_id": "seedance",
        "resolution": "720p",
        "seed": None,
        "requested_generate_audio": True,
        "content_mode": "drama",
        "utterances": [{"kind": "dialogue", "speaker": "Alice", "text": "Run"}],
        "has_utterances": True,
    }
    characters = {
        "Alice": {"voice_style": "bright"},
        "Bob": {"voice_style": "deep"},
    }

    original = build_storyboard_video_visual_basis(**common, voice_characters=characters)
    unrelated_change = build_storyboard_video_visual_basis(
        **common,
        voice_characters={**characters, "Bob": {"voice_style": "soft"}},
    )
    used_voice_change = build_storyboard_video_visual_basis(
        **common,
        voice_characters={**characters, "Alice": {"voice_style": "soft"}},
    )

    assert original.digest == unrelated_change.digest
    assert original.digest != used_voice_change.digest
