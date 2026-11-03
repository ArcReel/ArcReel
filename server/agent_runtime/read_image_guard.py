"""通用 Read 工具读图时的体积/尺寸硬防护。

SDK 的内置 Read 工具把图片文件整张以 base64 内联进上下文（PNG 无损、单帧可达
数十万字符）。子智能体一旦绕过正规看画面通道（``mcp__arcreel__inspect_video_units``）
改用 Bash+ffmpeg 抽原图再 Read，几张大图叠进上下文后，下一次带着这坨负载发往上游
模型的请求会长时间不返回，表现为"卡顿"。

这里在 Read 把文件读成图片内容块之前截一刀：PreToolUse hook 嗅探 Read 的目标，
命中"图片且超阈"时把它降采样并转码成 JPEG 落到项目内的缓存目录，再通过
``updatedInput`` 把 Read 的 ``file_path`` 改指到转码产物。Read 照常成功、Agent 照常
看到画面，只是内联的是一张受控体积的 JPEG，而非原始大图。

阈值依据：Anthropic 视觉接口在模型看到图片前，会把图片降到长边至多 1568 px、
总像素约 1.15 MP（见 https://docs.anthropic.com/en/docs/vision ）。因此长边超过
1568 px 的图片，多出的像素在模型侧只会被丢弃，白白占满上下文字节。本防护据此把
长边收敛到 1568 px，并转成 JPEG（质量 80，与项目联系表 ``lib.video_review.contact_sheet``
的 85 同量级、单帧略压一档）以去掉 PNG 无损编码的体积。字节阈值只用来放过本就很小的
图（转码得不偿失），不改变像素判据。

这道防护挂在对所有工具调用生效的 PreToolUse 匹配器上，故对任意子智能体（不止
review-footage）与主对话的 Read 读图一律生效。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from lib.infra.async_thread import run_sync_transaction

logger = logging.getLogger(__name__)

#: Read 转码产物的落点（项目 cwd 内，随项目缓存一并清理）。读该路径在项目内，受
#: ``_check_read_access`` 放行；主进程落盘不经内核沙箱 denyWrite。
_CACHE_DIRNAME = Path(".cache") / "read_image_guard"

#: 长边像素上限。默认 1568 与 Anthropic 视觉接口的降采样上限对齐：更大的边长在模型侧
#: 会被丢弃，只会白占上下文字节。可用 ``ARCREEL_READ_IMAGE_MAX_EDGE`` 覆盖。
_DEFAULT_MAX_EDGE = 1568

#: 字节阈值：原图不大于它且长边不超限时，转码收益有限，直接放过不改。默认 256 KiB，
#: 对应任务里实测单帧全分辨率 PNG 约 263 KB 的量级。可用 ``ARCREEL_READ_IMAGE_MAX_BYTES`` 覆盖。
_DEFAULT_MAX_BYTES = 256 * 1024

#: 转码 JPEG 质量。默认 80，和联系表的 85 同量级、单帧略压一档。
#: 可用 ``ARCREEL_READ_IMAGE_JPEG_QUALITY`` 覆盖。
_DEFAULT_JPEG_QUALITY = 80

#: Pillow 能解码并按图片内联的扩展名——只对这些动手，其余（文本、JSON、脚本）一律不碰。
#: 与 SDK Read 以图片块内联的类型对齐；SVG 不是位图，不处理。
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"})


def _int_env(key: str, default: int) -> int:
    """读一个正整数环境变量；缺省、空白或非法时退回 ``default``。"""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("read_image_guard: 环境变量 %s=%r 不是整数，退回默认 %d", key, raw, default)
        return default
    return value if value > 0 else default


def _max_edge() -> int:
    return _int_env("ARCREEL_READ_IMAGE_MAX_EDGE", _DEFAULT_MAX_EDGE)


def _max_bytes() -> int:
    return _int_env("ARCREEL_READ_IMAGE_MAX_BYTES", _DEFAULT_MAX_BYTES)


def _jpeg_quality() -> int:
    quality = _int_env("ARCREEL_READ_IMAGE_JPEG_QUALITY", _DEFAULT_JPEG_QUALITY)
    return min(quality, 100)


def _resolve(project_cwd: Path, file_path: str) -> Path:
    p = Path(file_path)
    return p.resolve() if p.is_absolute() else (project_cwd / p).resolve()


@dataclass(frozen=True, slots=True)
class _Transcoded:
    """转码产物及其落点和原始/新尺寸、字节数，供日志与改指 file_path 使用。"""

    target: Path
    jpeg: bytes
    original_size: tuple[int, int]
    new_size: tuple[int, int]
    original_bytes: int
    source_name: str


def _transcode(data: bytes, *, max_edge: int, quality: int) -> tuple[bytes, tuple[int, int], tuple[int, int]] | None:
    """把图片字节降采样并转成 JPEG。

    返回 ``(jpeg_bytes, 原尺寸, 新尺寸)``；无法解码时返回 ``None``（交由 Read 原样处理）。
    长边不超 ``max_edge`` 时不缩放，仅转码去掉 PNG 无损体积。多帧图（GIF/TIFF）只取首帧，
    透明通道铺白底——看画面用途够用，且避免 JPEG 不支持 alpha 的报错。
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            original_size = (image.width, image.height)
            frame = image.convert("RGB")
    except Exception:
        # Pillow 解码各种坏图/非图抛型繁杂，一律视作"不是可处理的位图"放过。
        return None

    long_edge = max(frame.width, frame.height)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        new_size = (max(1, round(frame.width * scale)), max(1, round(frame.height * scale)))
        frame = frame.resize(new_size, Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    frame.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue(), original_size, (frame.width, frame.height)


def _plan_transcode(project_cwd: Path, file_path: str) -> _Transcoded | None:
    """同步地读目标并尝试转码——纯 CPU + 阻塞读，由调用方卸到工作线程。

    仅当目标是图片扩展名、能被 Pillow 解码，且（原文件超字节阈或长边超像素阈），
    且转码后更小时，才返回转码计划；否则返回 ``None`` 表示"原样放行"。此函数不写盘。
    """
    resolved = _resolve(project_cwd, file_path)
    if resolved.suffix.lower() not in _IMAGE_SUFFIXES or not resolved.is_file():
        return None

    original_bytes = resolved.stat().st_size
    max_edge = _max_edge()
    data = resolved.read_bytes()
    transcoded = _transcode(data, max_edge=max_edge, quality=_jpeg_quality())
    if transcoded is None:
        return None
    jpeg, original_size, new_size = transcoded

    # 原图本就小且未超长边：转码无收益，原样放行。
    if original_bytes <= _max_bytes() and max(original_size) <= max_edge:
        return None
    # 转码后反而更大（罕见：本就压得很狠的小 JPEG）：原样放行，不徒增体积。
    if len(jpeg) >= original_bytes:
        return None

    # 产物名带原名与原字节数：便于排查，且同一文件改动后字节数变即换产物。
    target = project_cwd / _CACHE_DIRNAME / f"{resolved.stem}.{original_bytes}.jpg"
    return _Transcoded(
        target=target,
        jpeg=jpeg,
        original_size=original_size,
        new_size=new_size,
        original_bytes=original_bytes,
        source_name=resolved.name,
    )


def _write_transcoded(plan: _Transcoded) -> None:
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    plan.target.write_bytes(plan.jpeg)


def build_read_image_guard_hook(project_cwd: Path):
    """构造一个 PreToolUse hook：拦截 Read 读图，超阈图片转码为受控 JPEG 后放行。

    只处理 ``Read``，且仅当目标是图片扩展名、能被 Pillow 解码、且（原文件超字节阈
    或长边超像素阈）时才转码。转码产物落到项目内 ``.cache/read_image_guard/`` 并把
    ``file_path`` 改指到它，不拒绝、不弹窗、不改变 Read 的成功语义。任何意外都放过
    （返回空 dict），保证这道防护只会"少内联字节"，不会挡住 Read 本身。

    读目标 + Pillow 解码/缩放是阻塞的 CPU 工作，卸到工作线程；落盘走
    ``run_sync_transaction``，与装配器里其他文件写 hook 的做法一致，不堵事件循环。
    """

    async def _read_image_guard_hook(
        input_data: dict[str, Any],
        _tool_use_id: str | None,
        _context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Read":
            return {}
        tool_input = input_data.get("tool_input") or {}
        file_path = tool_input.get("file_path")
        if not file_path:
            return {}

        try:
            plan = await asyncio.to_thread(_plan_transcode, project_cwd, file_path)
            if plan is None:
                return {}

            await run_sync_transaction(_write_transcoded, plan)

            logger.info(
                "read_image_guard 转码放行: file=%s %dx%d/%dB → JPEG %dx%d/%dB redirect=%s",
                plan.source_name,
                plan.original_size[0],
                plan.original_size[1],
                plan.original_bytes,
                plan.new_size[0],
                plan.new_size[1],
                len(plan.jpeg),
                plan.target,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": {**tool_input, "file_path": str(plan.target)},
                },
            }
        except Exception:
            logger.exception("read_image_guard hook 异常，原样放行 Read: file=%s", file_path)
            return {}

    return _read_image_guard_hook


__all__ = ["build_read_image_guard_hook"]
