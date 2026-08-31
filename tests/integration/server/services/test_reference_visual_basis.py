"""Tests for reference_visual_basis."""

from __future__ import annotations

from pathlib import Path

from lib.reference_video.request_projection import resolve_reference_assets
from tests.integration.server.services.reference_video_tasks_support import (
    load_project_and_unit,
    write_project,
)


def test_reference_visual_basis_hashes_only_audio_sent_for_the_unit(tmp_path: Path) -> None:
    from lib.reference_video.request_projection import ProviderProjectionCandidate
    from server.services.narration_delivery_tasks import reference_video_visual_basis_digest

    proj_dir = write_project(tmp_path)
    project, unit = load_project_and_unit(proj_dir, "E1U1")
    project["characters"]["张三"].update(
        {
            "voice_style": "低沉男声",
            "reference_audio": "characters/refs_audio/张三.wav",
        }
    )
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "characters/张三.png",
        "voice_style": "清亮女声",
        "reference_audio": "characters/refs_audio/李四.wav",
    }
    unit["text"] = "@[张三] 站在门口。\n@[张三]：{出发。}"
    audio_dir = proj_dir / "characters" / "refs_audio"
    audio_dir.mkdir(parents=True)
    used_audio = audio_dir / "张三.wav"
    unrelated_audio = audio_dir / "李四.wav"
    used_audio.write_bytes(b"used-v1")
    unrelated_audio.write_bytes(b"unrelated-v1")
    candidate = ProviderProjectionCandidate(
        capability="r2v",
        provider_id="ark",
        model_id="doubao-seedance-2-0-260128",
        supported_durations=(4, 8, 12),
        max_reference_images=9,
        resolution="1080p",
        generate_audio=True,
        requested_generate_audio=True,
        has_audio_track=True,
        audio_switch_controllable=True,
        voice_consistency="native",
        max_reference_audio_count=3,
    )
    request_assets = resolve_reference_assets(project, proj_dir, unit)

    original = reference_video_visual_basis_digest(
        project=project,
        project_path=proj_dir,
        unit=unit,
        request_assets=request_assets,
        candidate=candidate,
    )
    unrelated_audio.write_bytes(b"unrelated-v2")
    after_unrelated_change = reference_video_visual_basis_digest(
        project=project,
        project_path=proj_dir,
        unit=unit,
        request_assets=request_assets,
        candidate=candidate,
    )
    used_audio.write_bytes(b"used-v2")
    after_used_change = reference_video_visual_basis_digest(
        project=project,
        project_path=proj_dir,
        unit=unit,
        request_assets=request_assets,
        candidate=candidate,
    )

    assert after_unrelated_change == original
    assert after_used_change != original
