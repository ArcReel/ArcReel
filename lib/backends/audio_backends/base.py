"""语音合成（TTS）服务层核心接口定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from lib.backends.audio_profiles import VoiceOption as VoiceOption


class AudioCapability(StrEnum):
    """音频后端支持的能力枚举。"""

    TEXT_TO_SPEECH = "text_to_speech"
    SPEECH_SPEED = "speech_speed"


@dataclass
class AudioSynthesisRequest:
    """通用语音合成请求。各 Backend 忽略不支持的字段。"""

    text: str
    output_path: Path
    voice: str
    language_type: str = "Chinese"
    speed: float | None = None


@dataclass
class AudioSynthesisResult:
    """通用语音合成结果。

    用量按用量契约上报（docs/adr/0100）：``text`` 是发给供应商的原始文本，按字符计价时字符数由计算器按价目表
    的「汉字计 2」属性统计；按 token 计价的模型另报 ``input_tokens``（输入 token）与 ``audio_output_tokens``
    （输出音频 token），供应商没有回报时为 ``None``。供应商回报了计费字符数时另报 ``billed_characters``，
    字符数计量项改用它，不再按文本统计。
    """

    provider: str
    model: str
    text: str
    output_path: Path
    input_tokens: int | None = None
    audio_output_tokens: int | None = None
    billed_characters: int | None = None


class AudioBackend(Protocol):
    """语音合成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> set[AudioCapability]: ...

    def list_voices(self) -> list[VoiceOption]: ...

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult: ...
