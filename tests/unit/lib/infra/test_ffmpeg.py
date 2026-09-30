"""随包 ffmpeg 查找器的自检结论（不启动子进程，由 run_version 注入自检结果）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.infra.ffmpeg import inspect_bundled_ffmpeg


def _binaries_dir(tmp_path: Path, *names: str) -> Path:
    directory = tmp_path / "binaries"
    directory.mkdir()
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "README.md").write_text("", encoding="utf-8")
    for name in names:
        (directory / name).write_bytes(b"")
    return directory


def _completed(returncode: int, stdout: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


def test_reports_the_bundled_binary_and_its_version(tmp_path: Path):
    directory = _binaries_dir(tmp_path, "ffmpeg-linux-x86_64-v7.0.2")

    status = inspect_bundled_ffmpeg(
        directory,
        run_version=lambda _exe: _completed(0, b"ffmpeg version 7.0.2-static Copyright (c)\nbuilt with gcc\n"),
    )

    assert status.available
    assert status.executable == directory / "ffmpeg-linux-x86_64-v7.0.2"
    assert status.version == "ffmpeg version 7.0.2-static Copyright (c)"


def test_unavailable_when_the_wheel_ships_no_binary(tmp_path: Path):
    status = inspect_bundled_ffmpeg(_binaries_dir(tmp_path), run_version=lambda _exe: _completed(0))

    assert not status.available
    assert status.executable is None
    assert status.unavailable_reason == "imageio-ffmpeg 未附带当前平台的 ffmpeg 可执行文件"


def test_unavailable_when_the_binary_cannot_start(tmp_path: Path):
    def _cannot_exec(_exe: Path) -> subprocess.CompletedProcess[bytes]:
        raise PermissionError("Permission denied")

    status = inspect_bundled_ffmpeg(_binaries_dir(tmp_path, "ffmpeg-linux-aarch64-v7.0.2"), run_version=_cannot_exec)

    assert not status.available
    assert status.unavailable_reason == "随包 ffmpeg 无法运行：Permission denied"


def test_unavailable_when_the_self_check_exits_non_zero(tmp_path: Path):
    status = inspect_bundled_ffmpeg(
        _binaries_dir(tmp_path, "ffmpeg-macos-aarch64-v7.1"), run_version=lambda _exe: _completed(1)
    )

    assert not status.available
    assert status.unavailable_reason == "随包 ffmpeg 自检失败：退出码 1"
