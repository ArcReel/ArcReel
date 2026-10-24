"""Saved refinement recipes use real local media without invoking a model."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lib.infra.ffmpeg import ffmpeg_executable
from scripts import refine_episode
from scripts.refine_episode import refine
from tests.factories import make_test_clip, wav_bytes


@pytest.fixture
def recipe_file(tmp_path: Path) -> Path:
    make_test_clip(tmp_path / "picture.mp4", size="160x96", fps=25, seconds=1, tone=True)
    (tmp_path / "voice.wav").write_bytes(wav_bytes(1.8, tone_hz=660))
    (tmp_path / "music.wav").write_bytes(wav_bytes(2.2, tone_hz=220))
    recipe = {
        "schema_version": 1,
        "width": 160,
        "height": 96,
        "fps": 25,
        "shots": [
            {"id": "shot-1", "file": "picture.mp4", "out_frame": 25},
            {"id": "shot-2", "file": "picture.mp4", "out_frame": 25},
        ],
        "required_tracks": ["narration", "music"],
        "narration": {"file": "voice.wav"},
        "music": {"file": "music.wav", "volume": 0.5},
    }
    path = tmp_path / "recipe.json"
    _write_recipe(path, recipe)
    return path


def _write_recipe(path: Path, recipe: dict[str, Any]) -> None:
    path.write_text(json.dumps(recipe), encoding="utf-8")


def _read_recipe(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_rejected(recipe_file: Path, message: str) -> None:
    output_dir = recipe_file.parent / "exports"
    with pytest.raises(ValueError, match=message):
        refine(recipe_file, output_dir)
    assert list(output_dir.rglob("episode.mp4")) == []
    assert list(output_dir.rglob("report.json")) == []


@pytest.mark.parametrize("track", ["narration", "music"])
def test_a_required_track_cannot_be_silently_omitted(recipe_file: Path, track: str) -> None:
    recipe = _read_recipe(recipe_file)
    del recipe[track]
    _write_recipe(recipe_file, recipe)

    _assert_rejected(recipe_file, f"Required {track} is missing")


@pytest.mark.parametrize("filename", ["voice.wav", "music.wav"])
def test_missing_required_audio_files_prevent_publication(recipe_file: Path, filename: str) -> None:
    (recipe_file.parent / filename).unlink()

    _assert_rejected(recipe_file, "Required input is missing")


def test_narration_that_would_be_truncated_prevents_publication(recipe_file: Path) -> None:
    recipe = _read_recipe(recipe_file)
    recipe["narration"]["start_seconds"] = 0.3
    _write_recipe(recipe_file, recipe)

    _assert_rejected(recipe_file, "Narration would be cut off")


def test_music_must_cover_the_entire_picture_edit(recipe_file: Path) -> None:
    (recipe_file.parent / "music.wav").write_bytes(wav_bytes(1.5, tone_hz=220))

    _assert_rejected(recipe_file, "Music must contain audio for the entire edit")


def test_trim_beyond_the_source_is_refused_without_inventing_a_freeze(recipe_file: Path) -> None:
    recipe = _read_recipe(recipe_file)
    recipe["shots"][0]["out_frame"] = 26
    _write_recipe(recipe_file, recipe)

    _assert_rejected(recipe_file, "asks for frames beyond its source")


def test_native_sound_cannot_double_the_replacement_voice(recipe_file: Path) -> None:
    recipe = _read_recipe(recipe_file)
    recipe["shots"][0]["source_volume"] = 0.5
    _write_recipe(recipe_file, recipe)

    _assert_rejected(recipe_file, "avoid doubled voices")


def test_duplicate_shot_ids_prevent_publication(recipe_file: Path) -> None:
    recipe = _read_recipe(recipe_file)
    recipe["shots"][1]["id"] = recipe["shots"][0]["id"]
    _write_recipe(recipe_file, recipe)

    _assert_rejected(recipe_file, "Shot IDs must be unique")


def _tone_amplitude(samples: tuple[int, ...], frequency: int) -> float:
    # Project onto each fixture tone; phases can change during AAC encoding.
    phase = 2 * math.pi * frequency / 8000
    real = sum(sample * math.cos(phase * index) for index, sample in enumerate(samples))
    imaginary = sum(sample * math.sin(phase * index) for index, sample in enumerate(samples))
    return 2 * math.hypot(real, imaginary) / len(samples)


def test_continuous_tracks_export_with_a_saved_report_and_exact_repeat_reuse(recipe_file: Path) -> None:
    output_dir = recipe_file.parent / "exports"
    source_bytes = {
        filename: (recipe_file.parent / filename).read_bytes() for filename in ("picture.mp4", "voice.wav", "music.wav")
    }

    first = refine(recipe_file, output_dir)

    output = Path(first["output"])
    report_file = output.parent / "report.json"
    output_bytes, report_bytes = output.read_bytes(), report_file.read_bytes()
    output_mtime, report_mtime = output.stat().st_mtime_ns, report_file.stat().st_mtime_ns
    assert first["complete"] is True
    assert first["reused"] is False
    assert first["model_calls"] == 0
    assert first["narration_continuous"] is True
    assert first["music_continuous"] is True
    assert first["output_sha256"] == hashlib.sha256(output_bytes).hexdigest()
    saved_report = json.loads(report_bytes)
    assert saved_report == json.loads(json.dumps(first))
    assert first["acceptance"]["expected_duration"] == 2
    assert first["acceptance"]["technical_review"]["decode_verified"] is True
    assert json.loads((output.parent.parent / "progress.json").read_bytes())["stage"] == "ready_for_review"

    decoded = subprocess.run(
        [
            ffmpeg_executable(),
            "-v",
            "error",
            "-i",
            str(output),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout
    samples = struct.unpack(f"<{len(decoded) // 2}h", decoded)
    # Both tracks survive before, across, and after the picture cut at one second.
    for start in (0.4, 0.9, 1.4):
        window = samples[round(start * 8000) : round((start + 0.2) * 8000)]
        assert _tone_amplitude(window, 660) > 3000
        assert _tone_amplitude(window, 220) > 1500
        assert _tone_amplitude(window, 440) < 300

    second = refine(recipe_file, output_dir)

    assert second == {**saved_report, "reused": True}
    assert output.read_bytes() == output_bytes
    assert report_file.read_bytes() == report_bytes
    assert output.stat().st_mtime_ns == output_mtime
    assert report_file.stat().st_mtime_ns == report_mtime
    assert list(output_dir.glob("*/finished/episode.mp4")) == [output]
    assert all((recipe_file.parent / filename).read_bytes() == content for filename, content in source_bytes.items())


def test_changed_source_creates_a_new_export_and_preserves_the_prior_version(recipe_file: Path) -> None:
    output_dir = recipe_file.parent / "exports"
    first = refine(recipe_file, output_dir)
    first_output = Path(first["output"])
    first_bytes = first_output.read_bytes()
    first_report = (first_output.parent / "report.json").read_bytes()
    music = recipe_file.parent / "music.wav"
    music.write_bytes(wav_bytes(2.2, tone_hz=330))

    changed = refine(recipe_file, output_dir)

    changed_output = Path(changed["output"])
    assert changed["complete"] is True
    assert changed["reused"] is False
    assert changed["fingerprint"] != first["fingerprint"]
    assert changed_output != first_output
    assert changed_output.read_bytes() != first_bytes
    assert changed["source_sha256"][str(music.resolve())] != first["source_sha256"][str(music.resolve())]
    assert first_output.read_bytes() == first_bytes
    assert (first_output.parent / "report.json").read_bytes() == first_report
    assert set(output_dir.glob("*/finished/episode.mp4")) == {first_output, changed_output}


def test_failed_report_publication_can_retry_without_an_orphaned_export(
    recipe_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = recipe_file.parent / "exports"
    write_report = refine_episode.atomic_write_json

    def fail_report(destination: Path, data: Any) -> None:
        if destination.name == "report.json":
            raise OSError(errno.ENOSPC, "Report storage is full")
        write_report(destination, data)

    with monkeypatch.context() as boundary:
        boundary.setattr(refine_episode, "atomic_write_json", fail_report)
        with pytest.raises(OSError, match="Report storage is full"):
            refine(recipe_file, output_dir)

    assert list(output_dir.glob("*/finished")) == []
    assert list(output_dir.rglob("episode.mp4")) == []
    assert list(output_dir.rglob("report.json")) == []
    progress = next(output_dir.glob("*/progress.json"))
    assert json.loads(progress.read_bytes())["stage"] == "failed"

    recovered = refine(recipe_file, output_dir)

    assert recovered["complete"] is True
    assert recovered["reused"] is False
    output = Path(recovered["output"])
    assert output.is_file()
    assert (output.parent / "report.json").is_file()
    assert sorted(path.name for path in output.parent.iterdir()) == ["episode.mp4", "report.json"]


def test_changed_published_output_is_preserved_and_never_reused(recipe_file: Path) -> None:
    output_dir = recipe_file.parent / "exports"
    first = refine(recipe_file, output_dir)
    output = Path(first["output"])
    report_file = output.parent / "report.json"
    report_bytes = report_file.read_bytes()
    output.write_bytes(b"An unrelated output now occupies this location")
    changed_bytes = output.read_bytes()

    with pytest.raises(ValueError, match="unverified output already exists"):
        refine(recipe_file, output_dir)

    assert output.read_bytes() == changed_bytes
    assert report_file.read_bytes() == report_bytes


def test_moving_an_export_returns_its_current_verified_path(recipe_file: Path) -> None:
    original_dir = recipe_file.parent / "original-exports"
    first = refine(recipe_file, original_dir)
    original_output = Path(first["output"])
    output_bytes = original_output.read_bytes()
    report_bytes = (original_output.parent / "report.json").read_bytes()
    moved_dir = recipe_file.parent / "moved-exports"
    shutil.move(original_dir, moved_dir)

    reused = refine(recipe_file, moved_dir)

    output = Path(reused["output"])
    assert reused["reused"] is True
    assert output == moved_dir / original_output.relative_to(original_dir)
    assert output.read_bytes() == output_bytes
    assert (output.parent / "report.json").read_bytes() == report_bytes
