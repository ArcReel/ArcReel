"""Read 读图硬防护：超阈大图被降采样转 JPEG 并改指产物，小图与非图片原样放行。

防护挂在对所有工具生效的 PreToolUse hook 上，故对任意子智能体与主对话的 Read 都生效。
这里直接驱动 hook 回调，构造图片/文本文件断言三类分支。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from server.agent_runtime.read_image_guard import build_read_image_guard_hook


def _png_bytes(width: int, height: int, color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def _run(project_cwd: Path, file_path: str) -> dict:
    hook = build_read_image_guard_hook(project_cwd)
    return await hook(
        {"tool_name": "Read", "tool_input": {"file_path": file_path}},
        "toolu_1",
        None,
    )


@dataclass(frozen=True)
class _JpegFacts:
    exists: bool
    fmt: str
    long_edge: int
    size: int


def _jpeg_facts(path: Path) -> _JpegFacts:
    """在同步上下文读产物事实，避免在 async 测试体里直接做阻塞 Path 调用（ruff ASYNC）。"""
    if not path.is_file():
        return _JpegFacts(exists=False, fmt="", long_edge=0, size=0)
    with Image.open(path) as out:
        return _JpegFacts(
            exists=True, fmt=out.format or "", long_edge=max(out.width, out.height), size=path.stat().st_size
        )


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _cache_dir_exists(project_cwd: Path) -> bool:
    return (project_cwd / ".cache" / "read_image_guard").exists()


@pytest.mark.asyncio
async def test_oversized_image_is_transcoded_to_smaller_jpeg(tmp_path: Path) -> None:
    """超阈大图：Read 被改指到一张 JPEG 产物，且像素长边与字节都明显下降。"""
    big = tmp_path / "frame.png"
    original = _png_bytes(4000, 2250)  # 长边 4000 px，远超 1568
    big.write_bytes(original)

    result = await _run(tmp_path, str(big))

    updated = result["hookSpecificOutput"]["updatedInput"]
    target = Path(updated["file_path"])
    assert target.suffix == ".jpg"

    facts = _jpeg_facts(target)
    assert facts.exists
    assert facts.fmt == "JPEG"
    assert facts.long_edge == 1568  # 收敛到长边上限
    assert facts.size < _file_size(big)  # 字节显著下降
    # 原图纹丝不动，只是 Read 改读产物
    assert _file_size(big) == len(original)


@pytest.mark.asyncio
async def test_small_image_is_left_untouched(tmp_path: Path) -> None:
    """未超阈小图：不改 Read 输入，产物目录也不建。"""
    small = tmp_path / "thumb.png"
    small.write_bytes(_png_bytes(64, 64))

    result = await _run(tmp_path, str(small))

    assert result == {}
    assert not _cache_dir_exists(tmp_path)


@pytest.mark.asyncio
async def test_non_image_file_is_left_untouched(tmp_path: Path) -> None:
    """非图片文件（如剧本 JSON / 文本）：防护完全不介入。"""
    script = tmp_path / "episode_1.json"
    script.write_text('{"a": 1}' * 100000, encoding="utf-8")  # 体积很大但不是图片

    result = await _run(tmp_path, str(script))

    assert result == {}


@pytest.mark.asyncio
async def test_non_read_tool_is_ignored(tmp_path: Path) -> None:
    """只对 Read 生效：其他工具调用原样放行。"""
    hook = build_read_image_guard_hook(tmp_path)
    big = tmp_path / "frame.png"
    big.write_bytes(_png_bytes(4000, 2250))

    result = await hook(
        {"tool_name": "Bash", "tool_input": {"file_path": str(big)}},
        "toolu_1",
        None,
    )

    assert result == {}


@pytest.mark.asyncio
async def test_threshold_is_configurable_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """像素长边上限可经 ARCREEL_READ_IMAGE_MAX_EDGE 覆盖。"""
    monkeypatch.setenv("ARCREEL_READ_IMAGE_MAX_EDGE", "512")
    big = tmp_path / "frame.png"
    big.write_bytes(_png_bytes(2000, 1000))

    result = await _run(tmp_path, str(big))

    target = Path(result["hookSpecificOutput"]["updatedInput"]["file_path"])
    assert _jpeg_facts(target).long_edge == 512
