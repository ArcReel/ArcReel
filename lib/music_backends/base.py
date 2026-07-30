"""音乐生成服务层核心接口定义与共享工具。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import httpx


async def download_audio_to_path(url: str, output_path: Path, *, timeout: int = 120) -> None:
    """从 URL 异步下载音频到本地文件。"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        content = resp.content

    def _save() -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)

    await asyncio.to_thread(_save)


class MusicCapability(StrEnum):
    """音乐后端支持的能力枚举。"""

    MUSIC_GENERATION = "music_generation"
    MUSIC_COVER = "music_cover"


@dataclass
class MusicGenerationRequest:
    """通用音乐生成请求。各 Backend 忽略不支持的字段。"""

    prompt: str
    output_path: Path
    # 唱词：None = 不带词（由 prompt 驱动）；空串与非空串一律原样透传。
    lyrics: str | None = None
    # 纯乐器伴奏（无人声）。
    is_instrumental: bool = False
    # 供应商侧改写 / 优化唱词。
    lyrics_optimizer: bool = False
    # 音频参数（采样率、比特率、format=mp3/wav/pcm 等）；空 dict = 用供应商默认。
    audio_setting: dict[str, Any] = field(default_factory=dict)
    # 输出格式：url（下载 24h 有效链接）或 hex（响应内嵌十六进制音频）。
    output_format: str = "url"
    project_name: str | None = None
    # 翻唱（music-cover）：二选一提供源音频；同时提供以 audio_url 优先。
    audio_url: str | None = None
    audio_base64: str | None = None
    cover_feature_id: str | None = None


@dataclass
class MusicGenerationResult:
    """通用音乐生成结果。"""

    music_path: Path
    provider: str
    model: str
    # 远端结果 URL（output_format=url）；hex 直落本地时为 None。
    audio_uri: str | None = None


class MusicBackend(Protocol):
    """音乐生成后端协议。"""

    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> set[MusicCapability]: ...
    async def generate(self, request: MusicGenerationRequest) -> MusicGenerationResult: ...


class MusicCapabilityError(RuntimeError):
    """音乐后端能力不匹配 / 输入不合规。

    与 ImageCapabilityError / VideoCapabilityError 对称：不携带本地化字符串，只带稳定 code +
    上下文 params；路由层直接 _t(code, **params) 渲染，Worker 则按 code + params 落
    task.error_message，文案留到读侧按 Accept-Language 渲染。
    """

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        super().__init__(code)
