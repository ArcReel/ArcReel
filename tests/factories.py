"""Test data factories — reduce boilerplate when constructing common objects."""

from __future__ import annotations

import subprocess
import wave
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from server.agent_runtime.models import SessionMeta


def make_translator(locale: str = "zh") -> Callable[..., str]:
    """Create a translator function bound to a fixed locale for testing."""
    from lib.i18n import _ as i18n_translate

    def translate(key: str, **kwargs) -> str:
        return i18n_translate(key, locale=locale, **kwargs)

    return translate


def wav_bytes(duration_seconds: float, sample_rate: int = 8000) -> bytes:
    """纯 stdlib 生成 wav 字节（不依赖 ffmpeg），供不要求真实音频编解码的用例使用。"""
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * int(duration_seconds * sample_rate))
    return buf.getvalue()


def make_test_video(path: Path, *, duration_sec: float = 1.0, fps: int = 30) -> None:
    """使用 ffmpeg 生成极短测试视频（64x64 像素）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=black:size=64x64:duration={duration_sec}:rate={fps}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def make_test_video_with_audio_tail(
    path: Path,
    *,
    video_duration_sec: float = 1.0,
    audio_duration_sec: float = 1.5,
    fps: int = 30,
) -> None:
    """生成音轨/容器尾部比视频轨更长的极短 MP4。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=black:size=64x64:duration={video_duration_sec}:rate={fps}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={audio_duration_sec}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def make_session_meta(**overrides) -> SessionMeta:
    """Build a SessionMeta with sensible defaults.

    Any keyword argument overrides the corresponding default field.
    """
    defaults = dict(
        id="session-1",
        project_name="demo",
        title="demo",
        status="running",
        created_at=datetime(2026, 2, 9, 8, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 2, 9, 8, 0, 0, tzinfo=UTC),
    )
    defaults.update(overrides)
    return SessionMeta(**defaults)


def make_task_params(**overrides) -> dict:
    """Build a dict of parameters suitable for ``GenerationQueue.enqueue_task()``.

    Any keyword argument overrides the corresponding default.
    """
    defaults = dict(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={"prompt": "test"},
        script_file="episode_01.json",
        source="webui",
    )
    defaults.update(overrides)
    return defaults


def make_sdk_transcript_entry(
    uuid: str, parent: str | None, entry_type: str, session_id: str, text: str
) -> dict[str, Any]:
    """一条 SDK 形态的 transcript 条目，供前缀分叉相关的测试搭建原会话历史。"""
    return {
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "type": entry_type,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": entry_type, "content": text},
    }


def make_transcript_entry(
    msg_type: str = "assistant",
    text: str = "hello",
    *,
    uuid: str = "msg-1",
    tool_use_id: str | None = None,
    tool_name: str | None = None,
    **extra,
) -> dict:
    """Build a single transcript JSONL entry dict.

    ``msg_type`` is one of ``"user"``, ``"assistant"``, ``"result"``.
    """
    if msg_type == "user":
        content = text
    elif msg_type == "result":
        entry: dict = {
            "type": "result",
            "subtype": extra.get("subtype", "success"),
            "is_error": extra.get("is_error", False),
            "uuid": uuid,
        }
        entry.update(extra)
        return entry
    else:
        if tool_use_id:
            content = [{"type": "tool_use", "id": tool_use_id, "name": tool_name or "Tool", "input": {}}]
        else:
            content = [{"type": "text", "text": text}]

    entry = {"type": msg_type, "message": {"content": content}, "uuid": uuid}
    entry.update(extra)
    return entry
