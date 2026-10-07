"""MP4 faststart：顶层 box 解析与重封装的降级路径，子进程由 spawn 替身驱动。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lib.infra.ffmpeg import FfmpegUnavailableError
from lib.infra.mp4_faststart import Mp4Layout, ensure_faststart, read_mp4_layout
from tests.fakes import HangingProcess


def _box(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _large_box(kind: bytes, payload: bytes = b"") -> bytes:
    """size=1 + 64 位 largesize 的写法。"""
    return (1).to_bytes(4, "big") + kind + (16 + len(payload)).to_bytes(8, "big") + payload


_FTYP = _box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2mp41")
_FTYP_QT = _box(b"ftyp", b"qt  " + b"\x00\x00\x02\x00" + b"qt  ")
_MOOV = _box(b"moov", _box(b"mvhd", b"\x00" * 100))
_MDAT = _box(b"mdat", b"\x01" * 256)

MOOV_FIRST = _FTYP + _MOOV + _MDAT
MOOV_LAST = _FTYP + _box(b"free") + _MDAT + _MOOV


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


class TestReadMp4Layout:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            pytest.param(MOOV_FIRST, Mp4Layout(major_brand=b"isom", moov_first=True), id="moov-first"),
            pytest.param(MOOV_LAST, Mp4Layout(major_brand=b"isom", moov_first=False), id="moov-last"),
            # 64 位 largesize 的 mdat 照样越过
            pytest.param(
                _FTYP + _large_box(b"mdat", b"\x01" * 64) + _MOOV,
                Mp4Layout(major_brand=b"isom", moov_first=False),
                id="largesize-mdat",
            ),
            # 首个 box 不是 ftyp：老式 QuickTime
            pytest.param(_box(b"wide") + _MDAT + _MOOV, Mp4Layout(major_brand=None, moov_first=False), id="no-ftyp"),
            pytest.param(_FTYP_QT + _MDAT + _MOOV, Mp4Layout(major_brand=b"qt  ", moov_first=False), id="quicktime"),
            # ftyp 装不下 major brand：不越界去读后一个 box 的字节
            pytest.param(_box(b"ftyp") + _MDAT + _MOOV, Mp4Layout(major_brand=None, moov_first=False), id="empty-ftyp"),
        ],
    )
    def test_reports_moov_position(self, tmp_path: Path, content: bytes, expected: Mp4Layout):
        assert read_mp4_layout(_write(tmp_path, "v.mp4", content)) == expected

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(b"", id="empty"),
            pytest.param(b"x" * 1024, id="not-iso-bmff"),
            pytest.param(_FTYP + _MDAT, id="no-moov"),
            pytest.param(_FTYP + _MOOV, id="no-mdat"),
            # size=0 的 mdat 延伸到文件尾，其后不可能再有 moov
            pytest.param(_FTYP + (0).to_bytes(4, "big") + b"mdat" + b"\x01" * 32, id="mdat-to-eof"),
            pytest.param(_FTYP + _MDAT[:-10] + _MOOV[:4], id="truncated"),
            pytest.param(_FTYP + (4).to_bytes(4, "big") + b"mdat" + _MOOV, id="size-below-header"),
        ],
    )
    def test_unrecognised_layout_is_none(self, tmp_path: Path, content: bytes):
        assert read_mp4_layout(_write(tmp_path, "v.mp4", content)) is None

    def test_quicktime_layouts_remux_with_the_mov_muxer(self):
        assert Mp4Layout(major_brand=b"qt  ", moov_first=False).muxer == "mov"
        assert Mp4Layout(major_brand=None, moov_first=False).muxer == "mov"
        assert Mp4Layout(major_brand=b"mp42", moov_first=False).muxer == "mp4"


class _ExitedProcess:
    """已退出的子进程替身（asyncio Process 形状）。"""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


def _write_output(ffmpeg_args: tuple[str, ...], payload: bytes) -> None:
    """ffmpeg 的输出路径是末位参数。"""
    Path(ffmpeg_args[-1]).write_bytes(payload)


def _remux_writing(payload: bytes | None, *, returncode: int = 0) -> tuple[Callable[..., Any], list[tuple[str, ...]]]:
    """spawn 替身：向 ffmpeg 输出路径（末位参数）写入 ``payload`` 后以 ``returncode`` 退出。"""
    calls: list[tuple[str, ...]] = []

    async def spawn(*args: str, **_kwargs: Any) -> _ExitedProcess:
        calls.append(args)
        if payload is not None:
            _write_output(args, payload)
        return _ExitedProcess(returncode)

    return spawn, calls


def _ffmpeg() -> str:
    return "/bundled/ffmpeg"


def _ffmpeg_unavailable() -> str:
    raise FfmpegUnavailableError("imageio-ffmpeg 未附带当前平台的 ffmpeg 可执行文件")


def _siblings(path: Path) -> list[Path]:
    return sorted(path.parent.iterdir())


class TestEnsureFaststart:
    async def test_moov_last_file_is_replaced_by_the_remuxed_output(self, tmp_path: Path):
        video = _write(tmp_path, "clip.mp4.part", MOOV_LAST)
        spawn, calls = _remux_writing(MOOV_FIRST)

        assert await ensure_faststart(video, resolve_ffmpeg=_ffmpeg, spawn=spawn) is True

        assert video.read_bytes() == MOOV_FIRST
        assert _siblings(video) == [video]
        # 无扩展名的 .part 也能重封装：封装格式显式给出
        args = calls[0]
        assert args[args.index("-f") + 1] == "mp4"
        assert args[args.index("-c") + 1] == "copy"
        assert "+faststart" in args

    async def test_moov_first_file_is_left_alone_without_spawning(self, tmp_path: Path):
        video = _write(tmp_path, "clip.mp4", MOOV_FIRST)
        spawn, calls = _remux_writing(MOOV_FIRST)

        assert await ensure_faststart(video, resolve_ffmpeg=_ffmpeg, spawn=spawn) is False

        assert video.read_bytes() == MOOV_FIRST
        assert calls == []

    async def test_unrecognised_file_is_left_alone(self, tmp_path: Path):
        video = _write(tmp_path, "clip.mp4", b"not a video")
        spawn, calls = _remux_writing(MOOV_FIRST)

        assert await ensure_faststart(video, resolve_ffmpeg=_ffmpeg, spawn=spawn) is False

        assert video.read_bytes() == b"not a video"
        assert calls == []

    async def test_missing_ffmpeg_keeps_original(self, tmp_path: Path):
        video = _write(tmp_path, "clip.mp4", MOOV_LAST)

        assert await ensure_faststart(video, resolve_ffmpeg=_ffmpeg_unavailable) is False

        assert video.read_bytes() == MOOV_LAST
        assert _siblings(video) == [video]

    @pytest.mark.parametrize(
        ("payload", "returncode"),
        [
            pytest.param(b"half written", 1, id="ffmpeg-fails"),
            pytest.param(MOOV_LAST, 0, id="output-still-moov-last"),
            pytest.param(b"garbage", 0, id="output-unrecognised"),
            pytest.param(None, 0, id="no-output"),
        ],
    )
    async def test_failed_remux_keeps_original_and_cleans_temp(
        self, tmp_path: Path, payload: bytes | None, returncode: int
    ):
        video = _write(tmp_path, "clip.mp4", MOOV_LAST)
        spawn, _ = _remux_writing(payload, returncode=returncode)

        assert await ensure_faststart(video, resolve_ffmpeg=_ffmpeg, spawn=spawn) is False

        assert video.read_bytes() == MOOV_LAST
        assert _siblings(video) == [video]

    async def test_temp_cleanup_failure_keeps_the_remux_best_effort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # 临时文件删不掉（如 Windows 上被其他进程占用）不能把尽力而为的优化变成失败
        video = _write(tmp_path, "clip.mp4", MOOV_LAST)
        spawn, _ = _remux_writing(MOOV_LAST)

        def _locked(self: Path, missing_ok: bool = False) -> None:
            raise PermissionError("file is in use")

        monkeypatch.setattr(Path, "unlink", _locked)

        assert await ensure_faststart(video, resolve_ffmpeg=_ffmpeg, spawn=spawn) is False

        assert video.read_bytes() == MOOV_LAST

    async def test_spawn_error_keeps_original(self, tmp_path: Path):
        video = _write(tmp_path, "clip.mp4", MOOV_LAST)

        async def spawn(*_args: str, **_kwargs: Any) -> _ExitedProcess:
            raise OSError("exec format error")

        assert await ensure_faststart(video, resolve_ffmpeg=_ffmpeg, spawn=spawn) is False

        assert video.read_bytes() == MOOV_LAST

    async def test_deadline_terminates_remux_and_keeps_original(self, tmp_path: Path):
        video = _write(tmp_path, "clip.mp4", MOOV_LAST)
        proc = HangingProcess(honors_terminate=True)

        async def spawn(*args: str, **_kwargs: Any) -> HangingProcess:
            _write_output(args, b"partial")
            return proc

        assert await ensure_faststart(video, deadline_seconds=0, resolve_ffmpeg=_ffmpeg, spawn=spawn) is False

        assert proc.signals == ["terminate"]
        assert video.read_bytes() == MOOV_LAST
        assert _siblings(video) == [video]
