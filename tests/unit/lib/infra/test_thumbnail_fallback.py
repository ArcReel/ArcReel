"""抽帧的降级、回退与 deadline：随包 ffmpeg 不可用时跳过，子进程由 spawn 替身驱动。"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import lib.infra.thumbnail as thumbnail_module
from lib.infra.ffmpeg import reset_for_tests
from tests.fakes import HangingProcess

_INPUT_INFO = b"Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'file:fake.mp4':\n"


def _framecrc(video_packets: int) -> bytes:
    """一个 25fps 视频流的 framecrc 输出，含 ``video_packets`` 个包。"""
    lines = [b"#tb 0: 1/12800", b"#media_type 0: video", b"#codec_id 0: h264"]
    lines += [f"0, {i * 512}, {i * 512}, 512, 100, 0x00000000".encode() for i in range(video_packets)]
    return b"\n".join(lines) + b"\n"


@pytest.fixture
def bundled_ffmpeg(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """保留真实查找器，只替换包目录定位与自检子进程边界。"""
    binaries_dir = tmp_path_factory.mktemp("bundled-ffmpeg")
    executable = binaries_dir / "ffmpeg-v7"
    executable.touch()
    reset_for_tests()
    with (
        patch("lib.infra.ffmpeg.importlib.resources.files", return_value=binaries_dir),
        patch("lib.infra.ffmpeg.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"ffmpeg v7\n")),
    ):
        try:
            yield str(executable)
        finally:
            reset_for_tests()


@pytest.fixture
def ffmpeg_unavailable(tmp_path: Path) -> Iterator[MagicMock]:
    reset_for_tests()
    with patch("lib.infra.ffmpeg.importlib.resources.files", return_value=tmp_path) as resources:
        try:
            yield resources
        finally:
            reset_for_tests()


def _is_probe(args: tuple[str, ...]) -> bool:
    return "framecrc" in args


def _is_decode_count(args: tuple[str, ...]) -> bool:
    return "-progress" in args


class _OutputProc:
    """一次性返回 stdout / stderr 的子进程替身。"""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


class _FfmpegProc:
    """抽帧子进程替身：按需向输出路径写入内容。"""

    returncode = 0

    def __init__(self, target: Path, payload: bytes | None = b"frame") -> None:
        self._target = target
        self._payload = payload

    async def wait(self) -> None:
        if self._payload is not None:
            self._target.write_bytes(self._payload)


async def test_returns_none_when_bundled_ffmpeg_unavailable(tmp_path: Path, ffmpeg_unavailable: MagicMock):
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"
    spawn = AsyncMock()

    result = await thumbnail_module.extract_video_thumbnail(video, out, spawn=spawn)

    assert result is None
    assert not out.exists()
    spawn.assert_not_called()


async def test_returns_none_when_video_missing(tmp_path: Path, ffmpeg_unavailable: MagicMock):
    """video 文件不存在时直接返回 None，不取随包 ffmpeg。"""
    result = await thumbnail_module.extract_video_thumbnail(tmp_path / "no-such-video.mp4", tmp_path / "out.jpg")

    assert result is None
    ffmpeg_unavailable.assert_not_called()


async def test_thumbnail_runs_the_bundled_ffmpeg(tmp_path: Path, bundled_ffmpeg: str):
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"
    call_log: list[tuple[str, ...]] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(args)
        return _FfmpegProc(Path(args[-1]))

    result = await thumbnail_module.extract_video_thumbnail(video, out, spawn=_spawn)

    assert result == out
    assert out.read_bytes() == b"frame"
    assert [args[0] for args in call_log] == [bundled_ffmpeg]


async def test_last_frame_returns_none_when_bundled_ffmpeg_unavailable(tmp_path: Path, ffmpeg_unavailable: MagicMock):
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    spawn = AsyncMock()

    result = await thumbnail_module.extract_video_last_frame(video, out, spawn=spawn)

    assert result is None
    assert not out.exists()
    spawn.assert_not_called()


async def test_last_frame_returns_none_when_video_missing(tmp_path: Path, ffmpeg_unavailable: MagicMock):
    result = await thumbnail_module.extract_video_last_frame(tmp_path / "no-such-video.mp4", tmp_path / "out.png")

    assert result is None
    ffmpeg_unavailable.assert_not_called()


async def test_last_frame_selects_the_last_packet_index(tmp_path: Path, bundled_ffmpeg: str):
    """快路径按解复用得到的视频包数定位最后一帧，不解码计帧。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    call_log: list[tuple[str, ...]] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(args)
        if _is_probe(args):
            return _OutputProc(_framecrc(30), _INPUT_INFO)
        return _FfmpegProc(Path(args[-1]))

    result = await thumbnail_module.extract_video_last_frame(video, out, spawn=_spawn)

    assert result == out
    assert len(call_log) == 2
    assert _is_probe(call_log[0])
    assert "select='eq(n\\,29)'" in call_log[1]


async def test_last_frame_falls_back_to_decoded_frame_count(tmp_path: Path, bundled_ffmpeg: str):
    """解复用读不到视频包时回退到全量解码计帧。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    call_log: list[tuple[str, ...]] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(args)
        if _is_probe(args):
            return _OutputProc(_framecrc(0), _INPUT_INFO)
        if _is_decode_count(args):
            return _OutputProc(b"frame=12\nprogress=continue\nframe=30\nprogress=end\n")
        return _FfmpegProc(Path(args[-1]))

    result = await thumbnail_module.extract_video_last_frame(video, out, spawn=_spawn)

    assert result == out
    assert len(call_log) == 3
    assert _is_probe(call_log[0])
    assert _is_decode_count(call_log[1])
    assert "select='eq(n\\,29)'" in call_log[2]


async def test_last_frame_retries_decoded_count_when_fast_extract_writes_nothing(tmp_path: Path, bundled_ffmpeg: str):
    """包数有值但 select 无输出时，用解码计帧结果重试并替换旧文件。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    out.write_bytes(b"stale")
    extract_payloads: list[bytes | None] = [None, b"fresh"]
    call_log: list[tuple[str, ...]] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(args)
        if _is_probe(args):
            return _OutputProc(_framecrc(40), _INPUT_INFO)
        if _is_decode_count(args):
            return _OutputProc(b"frame=30\nprogress=end\n")
        return _FfmpegProc(Path(args[-1]), extract_payloads.pop(0))

    result = await thumbnail_module.extract_video_last_frame(video, out, spawn=_spawn)

    assert result == out
    assert out.read_bytes() == b"fresh"
    assert len(call_log) == 4
    assert _is_probe(call_log[0])
    assert "select='eq(n\\,39)'" in call_log[1]
    assert _is_decode_count(call_log[2])
    assert "select='eq(n\\,29)'" in call_log[3]


_ZERO_DEADLINES = thumbnail_module.FrameExtractionDeadlines(probe=0, extract=0, count_frames=0, grace=0)


def _write_partial(target: str) -> None:
    Path(target).write_bytes(b"partial")


async def test_thumbnail_deadline_kills_ffmpeg_and_removes_partial_output(tmp_path: Path, bundled_ffmpeg: str):
    """ffmpeg 不退出时到 deadline 被终止，半成品被删除、已有缩略图保持不变，返回 None。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"
    out.write_bytes(b"previous")
    procs: list[HangingProcess] = []
    call_log: list[tuple[str, ...]] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(args)
        _write_partial(args[-1])
        proc = HangingProcess(honors_terminate=False)
        procs.append(proc)
        return proc

    result = await thumbnail_module.extract_video_thumbnail(video, out, deadlines=_ZERO_DEADLINES, spawn=_spawn)

    assert result is None
    assert out.read_bytes() == b"previous"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([video.name, out.name])  # noqa: ASYNC240 -- 断言阶段读取 tmp_path
    assert [p.signals for p in procs] == [["terminate", "kill"]]
    assert "-nostdin" in call_log[0]


async def test_last_frame_deadline_kills_ffmpeg_and_removes_temp_output(tmp_path: Path, bundled_ffmpeg: str):
    """按帧号抽帧的 ffmpeg 不退出时被终止，临时输出被删除，不产出尾帧。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    ffmpeg_procs: list[HangingProcess] = []

    async def _spawn(*args, **_kwargs):
        if _is_probe(args):
            return _OutputProc(_framecrc(30), _INPUT_INFO)
        if _is_decode_count(args):
            return _OutputProc(b"frame=30\nprogress=end\n")
        _write_partial(args[-1])
        proc = HangingProcess(honors_terminate=False)
        ffmpeg_procs.append(proc)
        return proc

    deadlines = thumbnail_module.FrameExtractionDeadlines(probe=3600, extract=0, count_frames=3600, grace=0)
    result = await thumbnail_module.extract_video_last_frame(video, out, deadlines=deadlines, spawn=_spawn)

    assert result is None
    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [video.name]  # noqa: ASYNC240 -- 断言阶段读取 tmp_path
    assert ffmpeg_procs
    assert all(p.returncode is not None for p in ffmpeg_procs)


async def test_last_frame_deadline_on_frame_count_returns_none(tmp_path: Path, bundled_ffmpeg: str):
    """计帧子进程不退出时被终止，不再继续抽帧。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    call_log: list[tuple[str, ...]] = []
    procs: list[HangingProcess] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(args)
        proc = HangingProcess(honors_terminate=True)
        procs.append(proc)
        return proc

    result = await thumbnail_module.extract_video_last_frame(video, out, deadlines=_ZERO_DEADLINES, spawn=_spawn)

    assert result is None
    assert len(call_log) == 2
    assert _is_probe(call_log[0])
    assert _is_decode_count(call_log[1])
    assert all(p.returncode is not None for p in procs)


async def test_concurrent_extractions_write_distinct_temp_files(tmp_path: Path, bundled_ffmpeg: str):
    """同一目标的并发抽帧各写各的临时文件（同目录、保留后缀），互不覆盖或删除。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"
    targets: list[Path] = []

    async def _spawn(*args, **_kwargs):
        targets.append(Path(args[-1]))
        return _FfmpegProc(Path(args[-1]))

    results = await asyncio.gather(
        thumbnail_module.extract_video_thumbnail(video, out, spawn=_spawn),
        thumbnail_module.extract_video_thumbnail(video, out, spawn=_spawn),
    )

    assert results == [out, out]
    assert len(set(targets)) == 2
    assert all(t.parent == out.parent and t.suffix == out.suffix for t in targets)
    assert out.read_bytes() == b"frame"


async def test_temp_output_removed_when_replacing_target_fails(tmp_path: Path, bundled_ffmpeg: str):
    """临时文件写成功但替换目标失败时返回 None，且不留下临时文件。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"
    out.mkdir()
    (out / "occupied").write_bytes(b"")

    async def _spawn(*args, **_kwargs):
        return _FfmpegProc(Path(args[-1]))

    result = await thumbnail_module.extract_video_thumbnail(video, out, spawn=_spawn)

    assert result is None
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([video.name, out.name])  # noqa: ASYNC240 -- 断言阶段读取 tmp_path
