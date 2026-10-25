"""Verify the exported guide actually contains the checked performance frames."""

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from lib.infra.ffmpeg import ffmpeg_executable
from scripts.prepare_contact_guide import (
    contact_sequence,
    export_guide,
    performed_reaction_sequence,
    render_depth,
    supported_reaction_sequence,
)


@pytest.mark.parametrize(
    ("reaction", "expressive", "continuation", "performed"),
    [
        (False, False, False, False),
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (False, False, False, True),
    ],
)
def test_export_decodes_all_frames_and_preserves_grayscale_contact(
    tmp_path, reaction, expressive, continuation, performed
):
    output = tmp_path / "guide"
    metrics = export_guide(
        output, reaction=reaction, expressive=expressive, continuation=continuation, performed=performed
    )
    video = output / "depth-guide.mp4"
    result = subprocess.run(
        [ffmpeg_executable(), "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True,
        capture_output=True,
    )
    decoded = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 224, 384, 3)
    sequence = (
        performed_reaction_sequence()
        if performed
        else supported_reaction_sequence()
        if continuation
        else contact_sequence(reaction=reaction, expressive=expressive)
    )
    viewport = (240, 0, 480, 280) if continuation or performed else None
    expected = np.stack([render_depth(frame, viewport=viewport) for frame in sequence])
    assert decoded.shape == expected.shape
    # YUV/RGB round trip can round grayscale levels by one code value.
    assert np.abs(decoded.astype(int) - expected.astype(int)).max() <= 1
    assert np.array_equal(decoded[:, :, :, 0], decoded[:, :, :, 1])
    assert np.array_equal(decoded[:, :, :, 0], decoded[:, :, :, 2])
    assert metrics["video_sha256"] == hashlib.sha256(video.read_bytes()).hexdigest()
    assert json.loads((output / "validation.json").read_text()) == metrics
    assert len(json.loads((output / "performance.json").read_text())["frames"]) == (97 if continuation else 49)
    runner_path = Path(__file__).resolve().parents[3] / "deploy/comfyui/mac_quality_video/runner.py"
    spec = importlib.util.spec_from_file_location("guide_runner_contract", runner_path)
    assert spec is not None
    assert spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    # Only the command boundary runs here; these files do not stand in for inference.
    model = tmp_path / "models/mlx/ltx-2.3-22b-q8-distilled-1.1"
    model.mkdir(parents=True)
    weights = model / "transformer-dev.safetensors"
    weights.touch()
    command = runner.build_command(
        {
            "name": "guide-contract",
            "mode": "structural-control",
            "width": 768,
            "height": 448,
            "frames": len(decoded),
            "seed": 1,
            "prompt": "A supported prop moves with its carrier.",
            "image": str(output / "frame-00.png"),
            "control_video": str(video),
            "control_lora": str(weights),
            "distilled_lora": str(weights),
        },
        tmp_path,
    )
    assert command[command.index("--frames") + 1] == str(len(decoded))
    with pytest.raises(FileExistsError):
        export_guide(output)
