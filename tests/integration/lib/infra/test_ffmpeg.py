"""随包 ffmpeg 查找器在真实环境中的取用：只认 imageio-ffmpeg 自带的二进制。"""

from __future__ import annotations

from pathlib import Path

import imageio_ffmpeg
import pytest

import lib.infra.ffmpeg as ffmpeg_module


@pytest.fixture(autouse=True)
def reset_ffmpeg_status():
    ffmpeg_module.reset_for_tests()
    yield
    ffmpeg_module.reset_for_tests()


def test_resolves_the_bundled_binary_without_system_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", str(tmp_path / "ffmpeg"))

    executable = Path(ffmpeg_module.ffmpeg_executable())
    status = ffmpeg_module.ffmpeg_status()

    assert executable.parent == Path(imageio_ffmpeg.__file__).parent / "binaries"
    assert executable.name.startswith("ffmpeg-")
    assert status.available
    assert status.version is not None
    assert status.version.startswith("ffmpeg version ")
