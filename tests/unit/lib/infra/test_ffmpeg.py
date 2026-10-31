"""随包 ffmpeg 查找器的自检结论（不启动子进程，由 run_version 注入自检结果）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.infra.ffmpeg import FfmpegStatus, inspect_bundled_ffmpeg

ENCODERS_OUTPUT = (
    "Encoders:\n"
    " V..... = Video\n"
    " A..... = Audio\n"
    " ------\n"
    " V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC\n"
    " A....D aac                  AAC (Advanced Audio Coding)\n"
)
FILTERS_OUTPUT = (
    "Filters:\n"
    "  T.. = Timeline support\n"
    "  | = Source or sink filter\n"
    " ------\n"
    " TSC aap               AA->A      Apply Affine Projection algorithm.\n"
    " ... loudnorm          A->A       EBU R128 loudness normalization\n"
    " .S. xfade             VV->V      Cross fade one video with another video.\n"
)


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


def test_self_check_lists_encoders_and_filters(tmp_path: Path):
    directory = _binaries_dir(tmp_path, "ffmpeg-linux-x86_64-v7.1")

    def _listing(_exe: Path, flag: str) -> subprocess.CompletedProcess[bytes]:
        output = ENCODERS_OUTPUT if flag == "-encoders" else FILTERS_OUTPUT
        return _completed(0, output.encode())

    status = inspect_bundled_ffmpeg(
        directory,
        run_version=lambda _exe: _completed(0, b"ffmpeg version 7.1-static\n"),
        run_listing=_listing,
    )

    assert status.available
    assert status.listed_encoders == frozenset({"libx264", "aac"})
    assert status.listed_filters == frozenset({"aap", "loudnorm", "xfade"})
    assert status.component_state("libx264", "encoder") == "available"
    assert status.component_state("vp9", "encoder") == "missing"
    assert status.component_state("loudnorm", "filter") == "available"


def test_failed_listing_reports_components_as_unknown(tmp_path: Path):
    directory = _binaries_dir(tmp_path, "ffmpeg-linux-x86_64-v7.1")

    status = inspect_bundled_ffmpeg(
        directory,
        run_version=lambda _exe: _completed(0, b"ffmpeg version 7.1-static\n"),
        run_listing=lambda _exe, _flag: _completed(1),
    )

    assert status.available
    assert status.listed_encoders is None
    assert status.listed_filters is None
    assert status.component_state("libx264", "encoder") == "unknown"


def test_unparseable_listing_reports_components_as_unknown(tmp_path: Path):
    directory = _binaries_dir(tmp_path, "ffmpeg-linux-x86_64-v7.1")

    status = inspect_bundled_ffmpeg(
        directory,
        run_version=lambda _exe: _completed(0, b"ffmpeg version 7.1-static\n"),
        run_listing=lambda _exe, _flag: _completed(0, b"no component lines here\n"),
    )

    assert status.available
    assert status.listed_encoders is None
    assert status.component_state("aac", "encoder") == "unknown"


def test_component_gaps_split_missing_and_unknown():
    status = FfmpegStatus(None, "7.1", None, listed_encoders=frozenset({"aac"}), listed_filters=None)

    missing, unknown = status.component_gaps({"encoder": {"aac", "libx264"}, "filter": {"loudnorm"}})

    assert missing == ("libx264",)
    assert unknown == ("loudnorm",)
