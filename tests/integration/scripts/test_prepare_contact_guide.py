"""Verify the exported guide actually contains the checked performance frames."""

import hashlib
import json
import subprocess

import numpy as np
import pytest

from lib.infra.ffmpeg import ffmpeg_executable
from scripts.prepare_contact_guide import contact_sequence, export_guide, render_depth, supported_reaction_sequence


@pytest.mark.parametrize(
    ("reaction", "expressive", "continuation"),
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
)
def test_export_decodes_all_frames_and_preserves_grayscale_contact(tmp_path, reaction, expressive, continuation):
    output = tmp_path / "guide"
    metrics = export_guide(output, reaction=reaction, expressive=expressive, continuation=continuation)
    video = output / "depth-guide.mp4"
    result = subprocess.run(
        [ffmpeg_executable(), "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        check=True,
        capture_output=True,
    )
    decoded = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 224, 384, 3)
    sequence = (
        supported_reaction_sequence() if continuation else contact_sequence(reaction=reaction, expressive=expressive)
    )
    viewport = (240, 0, 480, 280) if continuation else None
    expected = np.stack([render_depth(frame, viewport=viewport) for frame in sequence])
    assert decoded.shape == expected.shape
    # YUV/RGB round trip can round grayscale levels by one code value.
    assert np.abs(decoded.astype(int) - expected.astype(int)).max() <= 1
    assert np.array_equal(decoded[:, :, :, 0], decoded[:, :, :, 1])
    assert np.array_equal(decoded[:, :, :, 0], decoded[:, :, :, 2])
    assert metrics["video_sha256"] == hashlib.sha256(video.read_bytes()).hexdigest()
    assert json.loads((output / "validation.json").read_text()) == metrics
    assert len(json.loads((output / "performance.json").read_text())["frames"]) == (97 if continuation else 49)
    with pytest.raises(FileExistsError):
        export_guide(output)
