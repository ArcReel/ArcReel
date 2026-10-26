"""Reject malformed Ingredients input before allocating a video model."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.infra.ffmpeg import ffmpeg_executable

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def renderer():
    spec = importlib.util.spec_from_file_location(
        "identity_preflight_runner", ROOT / "deploy/comfyui/mac_quality_video/runner.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not (shutil.which("ffprobe") or Path("/opt/homebrew/bin/ffprobe").is_file()):
        pytest.skip("The standalone Mac renderer requires system ffprobe")
    return module


@pytest.mark.parametrize(
    ("frames", "fps", "width", "valid"),
    [(1, 24, 768, False), (120, 24, 768, False), (121, 12, 768, False), (121, 24, 512, False), (121, 24, 768, True)],
)
def test_real_reference_contract_before_model_launch(renderer, tmp_path, monkeypatch, frames, fps, width, valid):
    reference = tmp_path / "reference.mp4"
    subprocess.run(
        [
            ffmpeg_executable(),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=blue:s={width}x448:r={fps}",
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(reference),
        ],
        check=True,
        timeout=30,
        capture_output=True,
    )
    model = tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1"
    model.mkdir(parents=True)
    (model / "transformer-dev.safetensors").touch()
    control, distilled = tmp_path / "control.safetensors", tmp_path / "distilled.safetensors"
    control.touch()
    distilled.touch()
    config = {
        "name": "reference-contract",
        "mode": "identity-reference",
        "width": 768,
        "height": 448,
        "frames": 121,
        "seed": 1,
        "prompt": "A small head turn.",
        "control_video": str(reference),
        "control_lora": str(control),
        "distilled_lora": str(distilled),
    }
    if valid:
        renderer.validate_identity_reference(config)
        return
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="reference must contain 121 frames"):
        renderer.run(path)
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "user").exists()


def test_unreadable_reference_fails_closed(renderer, tmp_path):
    reference = tmp_path / "broken.mp4"
    reference.write_bytes(b"not a video")
    with pytest.raises(ValueError, match="Cannot verify the Ingredients reference"):
        renderer.validate_identity_reference({"mode": "identity-reference", "control_video": str(reference)})
