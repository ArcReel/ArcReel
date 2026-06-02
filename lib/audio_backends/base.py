"""语音合成（TTS）服务层核心接口定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class AudioCapability(StrEnum):
    """音频后端支持的能力枚举。"""

    TEXT_TO_SPEECH = "text_to_speech"


@dataclass
class AudioSynthesisRequest:
    """通用语音合成请求。各 Backend 忽略不支持的字段。"""

    text: str
    output_path: Path
    voice: str
    language_type: str = "Chinese"
    # 语速预留：同步 qwen3-tts-flash 不支持（speech_rate 仅 realtime WebSocket 版可用），
    # 后端记 debug log 忽略。保留字段以便将来接入实时/可调速后端。
    speed: float | None = None


@dataclass
class AudioSynthesisResult:
    """通用语音合成结果。``characters`` 驱动按字符计费。"""

    provider: str
    model: str
    characters: int
    output_path: Path


class AudioBackend(Protocol):
    """语音合成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> set[AudioCapability]: ...

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult: ...
