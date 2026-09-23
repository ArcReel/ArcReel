"""Thumbnail extraction graceful-skip when ffmpeg is unavailable."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import lib.infra.thumbnail as thumbnail_module
from tests.fakes import HangingProcess


@pytest.fixture(autouse=True)
def reset_ffmpeg_cache():
    thumbnail_module.reset_for_tests()
    yield
    thumbnail_module.reset_for_tests()


@pytest.mark.asyncio
async def test_returns_none_when_ffmpeg_missing(tmp_path: Path):
    """ffmpeg 不在 PATH 中时不应 spawn 子进程，直接返回 None。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")  # nominal file; we never actually decode
    out = tmp_path / "out.jpg"

    spawn = AsyncMock()
    with patch("lib.infra.thumbnail.shutil.which", return_value=None):
        result = await thumbnail_module.extract_video_thumbnail(video, out, spawn=spawn)

    assert result is None
    assert not out.exists()
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_returns_none_when_video_missing(tmp_path: Path):
    """video 文件不存在时直接返回 None，不检查 ffmpeg。"""
    nonexistent = tmp_path / "no-such-video.mp4"
    out = tmp_path / "out.jpg"

    with patch("lib.infra.thumbnail.shutil.which") as which:
        result = await thumbnail_module.extract_video_thumbnail(nonexistent, out)

    assert result is None
    which.assert_not_called()


@pytest.mark.asyncio
async def test_ffmpeg_available_attempts_extraction(tmp_path: Path):
    """ffmpeg 在 PATH 时走原有 spawn 路径（spawn 被调用，returncode 非零仍返回 None）。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"

    class _FakeProc:
        returncode = 1  # ffmpeg failure

        async def wait(self):
            return None

    spawn = AsyncMock(return_value=_FakeProc())
    with patch("lib.infra.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"):
        result = await thumbnail_module.extract_video_thumbnail(video, out, spawn=spawn)

    assert result is None
    spawn.assert_called_once()


def test_ffmpeg_available_is_cached():
    """_ffmpeg_available() 用 @functools.cache：首次探测的结论此后固定，不随 PATH 再变。

    断言落在「结论不变」而非探测次数上——调用方依赖的是这个稳定结论（进程内不会一会儿有
    ffmpeg 一会儿没有），探测只调一次是它的副产物。
    """
    with patch("lib.infra.thumbnail.shutil.which", return_value=None):
        assert thumbnail_module._ffmpeg_available() is False
    with patch("lib.infra.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"):
        assert thumbnail_module._ffmpeg_available() is False


@pytest.mark.asyncio
async def test_last_frame_returns_none_when_ffmpeg_missing(tmp_path: Path):
    """ffmpeg 缺失时 extract_video_last_frame 直接返回 None，不 spawn 子进程。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"

    spawn = AsyncMock()
    with patch("lib.infra.thumbnail.shutil.which", return_value=None):
        result = await thumbnail_module.extract_video_last_frame(video, out, spawn=spawn)

    assert result is None
    assert not out.exists()
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_last_frame_returns_none_when_only_ffprobe_missing(tmp_path: Path):
    """ffmpeg 在 PATH 但 ffprobe 缺失（精简容器场景）时也应短路。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"

    def _which(name: str):
        return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

    spawn = AsyncMock()
    with patch("lib.infra.thumbnail.shutil.which", side_effect=_which):
        result = await thumbnail_module.extract_video_last_frame(video, out, spawn=spawn)

    assert result is None
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_last_frame_returns_none_when_video_missing(tmp_path: Path):
    """video 不存在时直接返回 None，不检查 ffmpeg/ffprobe。"""
    nonexistent = tmp_path / "no-such-video.mp4"
    out = tmp_path / "out.png"

    with patch("lib.infra.thumbnail.shutil.which") as which:
        result = await thumbnail_module.extract_video_last_frame(nonexistent, out)

    assert result is None
    which.assert_not_called()


@pytest.mark.asyncio
async def test_last_frame_falls_back_to_count_frames(tmp_path: Path):
    """容器 nb_frames 为 N/A 时回退到 -count_frames 路径。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"

    class _ProbeProc:
        """第一次返回 N/A，第二次返回 30。"""

        def __init__(self, payload: bytes, rc: int = 0):
            self._payload = payload
            self.returncode = rc

        async def communicate(self):
            return self._payload, b""

    class _FfmpegProc:
        def __init__(self, target: Path):
            self._target = target

        returncode = 0

        async def wait(self):
            # 模拟 ffmpeg 写出文件
            self._target.write_bytes(b"\x89PNG\r\n\x1a\n")
            return

    call_log: list[list[str]] = []
    procs = [
        _ProbeProc(b"N/A\n"),  # nb_frames 快路径
        _ProbeProc(b"30\n"),  # -count_frames 回退
    ]

    async def _spawn(*args, **_kwargs):
        call_log.append(list(args))
        if args[0] == "ffmpeg":
            return _FfmpegProc(Path(args[-1]))
        return procs.pop(0)

    def _which(name: str):
        return f"/usr/bin/{name}"

    with patch("lib.infra.thumbnail.shutil.which", side_effect=_which):
        result = await thumbnail_module.extract_video_last_frame(video, out, spawn=_spawn)

    assert result == out
    assert len(call_log) == 3
    assert call_log[0][0] == "ffprobe"
    assert "-count_frames" not in call_log[0]
    assert call_log[1][0] == "ffprobe"
    assert "-count_frames" in call_log[1]
    assert call_log[2][0] == "ffmpeg"


@pytest.mark.asyncio
async def test_last_frame_retries_precise_count_when_fast_extract_writes_nothing(
    tmp_path: Path,
):
    """nb_frames 有值但 select 无输出时，用 -count_frames 结果重试并替换旧文件。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    out.write_bytes(b"stale")

    class _ProbeProc:
        def __init__(self, payload: bytes):
            self._payload = payload
            self.returncode = 0

        async def communicate(self):
            return self._payload, b""

    class _FfmpegProc:
        returncode = 0

        def __init__(self, target: Path, *, writes_output: bool):
            self._target = target
            self._writes_output = writes_output

        async def wait(self):
            if self._writes_output:
                self._target.write_bytes(b"fresh")
            return

    probe_procs = [_ProbeProc(b"999\n"), _ProbeProc(b"30\n")]
    ffmpeg_writes = [False, True]
    call_log: list[list[str]] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(list(args))
        if args[0] == "ffprobe":
            return probe_procs.pop(0)
        return _FfmpegProc(Path(args[-1]), writes_output=ffmpeg_writes.pop(0))

    def _which(name: str):
        return f"/usr/bin/{name}"

    with patch("lib.infra.thumbnail.shutil.which", side_effect=_which):
        result = await thumbnail_module.extract_video_last_frame(video, out, spawn=_spawn)

    assert result == out
    assert out.read_bytes() == b"fresh"
    assert len(call_log) == 4
    assert call_log[0][0] == "ffprobe"
    assert "-count_frames" not in call_log[0]
    assert call_log[1][0] == "ffmpeg"
    assert call_log[2][0] == "ffprobe"
    assert "-count_frames" in call_log[2]
    assert call_log[3][0] == "ffmpeg"


def test_ffprobe_available_is_cached():
    """_ffprobe_available() 走 @functools.cache：首次结论定死，之后 which 换答案也不再重探。"""
    with patch("lib.infra.thumbnail.shutil.which", return_value=None):
        assert thumbnail_module._ffprobe_available() is False
    with patch("lib.infra.thumbnail.shutil.which", return_value="/usr/bin/ffprobe"):
        assert thumbnail_module._ffprobe_available() is False


_ZERO_DEADLINES = thumbnail_module.FrameExtractionDeadlines(probe=0, extract=0, count_frames=0, grace=0)


def _write_partial(target: str) -> None:
    Path(target).write_bytes(b"partial")


def _all_tools_available(name: str):
    return f"/usr/bin/{name}"


@pytest.mark.asyncio
async def test_thumbnail_deadline_kills_ffmpeg_and_removes_partial_output(tmp_path: Path):
    """ffmpeg 不退出时到 deadline 被终止，半成品被删除、已有缩略图保持不变，返回 None。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"
    out.write_bytes(b"previous")
    procs: list[HangingProcess] = []
    call_log: list[list[str]] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(list(args))
        _write_partial(args[-1])
        proc = HangingProcess(honors_terminate=False)
        procs.append(proc)
        return proc

    with patch("lib.infra.thumbnail.shutil.which", side_effect=_all_tools_available):
        result = await thumbnail_module.extract_video_thumbnail(video, out, deadlines=_ZERO_DEADLINES, spawn=_spawn)

    assert result is None
    assert out.read_bytes() == b"previous"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([video.name, out.name])  # noqa: ASYNC240 -- 断言阶段读取 tmp_path
    assert [p.signals for p in procs] == [["terminate", "kill"]]
    assert "-nostdin" in call_log[0]


@pytest.mark.asyncio
async def test_last_frame_deadline_kills_ffmpeg_and_removes_temp_output(tmp_path: Path):
    """按帧号抽帧的 ffmpeg 不退出时被终止，临时输出被删除，不产出尾帧。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    ffmpeg_procs: list[HangingProcess] = []

    class _ProbeProc:
        returncode = 0

        async def communicate(self):
            return b"30\n", b""

    async def _spawn(*args, **_kwargs):
        if args[0] == "ffprobe":
            return _ProbeProc()
        _write_partial(args[-1])
        proc = HangingProcess(honors_terminate=False)
        ffmpeg_procs.append(proc)
        return proc

    deadlines = thumbnail_module.FrameExtractionDeadlines(probe=3600, extract=0, count_frames=3600, grace=0)
    with patch("lib.infra.thumbnail.shutil.which", side_effect=_all_tools_available):
        result = await thumbnail_module.extract_video_last_frame(video, out, deadlines=deadlines, spawn=_spawn)

    assert result is None
    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [video.name]  # noqa: ASYNC240 -- 断言阶段读取 tmp_path
    assert ffmpeg_procs
    assert all(p.returncode is not None for p in ffmpeg_procs)


@pytest.mark.asyncio
async def test_last_frame_deadline_on_frame_count_probe_returns_none(tmp_path: Path):
    """ffprobe 计帧不退出时被终止，不再继续抽帧。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.png"
    call_log: list[list[str]] = []
    procs: list[HangingProcess] = []

    async def _spawn(*args, **_kwargs):
        call_log.append(list(args))
        proc = HangingProcess(honors_terminate=True)
        procs.append(proc)
        return proc

    with patch("lib.infra.thumbnail.shutil.which", side_effect=_all_tools_available):
        result = await thumbnail_module.extract_video_last_frame(video, out, deadlines=_ZERO_DEADLINES, spawn=_spawn)

    assert result is None
    assert [args[0] for args in call_log] == ["ffprobe", "ffprobe"]
    assert all(p.returncode is not None for p in procs)


@pytest.mark.asyncio
async def test_concurrent_extractions_write_distinct_temp_files(tmp_path: Path):
    """同一目标的并发抽帧各写各的临时文件（同目录、保留后缀），互不覆盖或删除。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"
    targets: list[Path] = []

    class _FfmpegProc:
        returncode = 0

        def __init__(self, target: Path):
            self._target = target

        async def wait(self):
            self._target.write_bytes(b"frame")

    async def _spawn(*args, **_kwargs):
        targets.append(Path(args[-1]))
        return _FfmpegProc(Path(args[-1]))

    with patch("lib.infra.thumbnail.shutil.which", side_effect=_all_tools_available):
        results = await asyncio.gather(
            thumbnail_module.extract_video_thumbnail(video, out, spawn=_spawn),
            thumbnail_module.extract_video_thumbnail(video, out, spawn=_spawn),
        )

    assert results == [out, out]
    assert len(set(targets)) == 2
    assert all(t.parent == out.parent and t.suffix == out.suffix for t in targets)
    assert out.read_bytes() == b"frame"


@pytest.mark.asyncio
async def test_temp_output_removed_when_replacing_target_fails(tmp_path: Path):
    """临时文件写成功但替换目标失败时返回 None，且不留下临时文件。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"\x00")
    out = tmp_path / "out.jpg"
    out.mkdir()
    (out / "occupied").write_bytes(b"")

    class _FfmpegProc:
        returncode = 0

        def __init__(self, target: Path):
            self._target = target

        async def wait(self):
            self._target.write_bytes(b"frame")

    async def _spawn(*args, **_kwargs):
        return _FfmpegProc(Path(args[-1]))

    with patch("lib.infra.thumbnail.shutil.which", side_effect=_all_tools_available):
        result = await thumbnail_module.extract_video_thumbnail(video, out, spawn=_spawn)

    assert result is None
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([video.name, out.name])  # noqa: ASYNC240 -- 断言阶段读取 tmp_path
