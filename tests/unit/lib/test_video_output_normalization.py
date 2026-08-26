from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.video_output_normalization import (
    VideoOutputNormalizationPolicy,
    normalize_video_output,
)


def _policy() -> VideoOutputNormalizationPolicy:
    return VideoOutputNormalizationPolicy.from_dict(
        {
            "schema_version": 1,
            "kind": "safe_frame_crop",
            "input": {"width": 720, "height": 1280},
            "crop": {"x": 0, "y": 0, "width": 612, "height": 1088},
            "output": {"width": 720, "height": 1280},
            "scale_filter": "lanczos",
        }
    )


def _synthetic_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=720x1280:d=0.4:r=5",
            "-vf",
            "drawbox=x=620:y=1100:w=80:h=80:color=red:t=fill,"
            "drawbox=x=500:y=900:w=50:h=50:color=green:t=fill",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def _first_rgb_frame(path: Path) -> bytes:
    return subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


def test_normalizes_exact_safe_frame_and_records_content_lineage(tmp_path: Path) -> None:
    raw = tmp_path / "provider-raw.mp4"
    normalized = tmp_path / "normalized.mp4"
    _synthetic_video(raw)

    receipt = normalize_video_output(raw, normalized, _policy())

    assert raw.is_file()
    assert normalized.is_file()
    assert receipt.input_dimensions == {"width": 720, "height": 1280}
    assert receipt.output_dimensions == {"width": 720, "height": 1280}
    assert receipt.filter_graph == "crop=612:1088:0:0,scale=720:1280:flags=lanczos"
    assert len(receipt.raw_sha256) == 64
    assert len(receipt.normalized_sha256) == 64
    assert receipt.raw_sha256 != receipt.normalized_sha256
    assert len(receipt.policy_digest) == 64
    assert receipt.ffmpeg_version.startswith("8.")

    frame = _first_rgb_frame(normalized)
    pixels = zip(frame[0::3], frame[1::3], frame[2::3], strict=True)
    red = 0
    green = 0
    for r, g, b in pixels:
        red += int(r > 180 and g < 100 and b < 100)
        green += int(g > 80 and r < 180 and b < 150)
    assert red == 0
    assert green > 500


def test_fails_closed_on_dimension_or_crop_drift(tmp_path: Path) -> None:
    raw = tmp_path / "provider-raw.mp4"
    normalized = tmp_path / "normalized.mp4"
    _synthetic_video(raw)
    wrong_input = VideoOutputNormalizationPolicy.from_dict(
        {
            **_policy().to_dict(),
            "input": {"width": 1080, "height": 1920},
        }
    )

    with pytest.raises(ValueError, match="input dimensions"):
        normalize_video_output(raw, normalized, wrong_input)
    assert not normalized.exists()

    with pytest.raises(ValueError, match="crop must stay inside input bounds"):
        VideoOutputNormalizationPolicy.from_dict(
            {
                **_policy().to_dict(),
                "crop": {"x": 700, "y": 1200, "width": 100, "height": 100},
            }
        )
