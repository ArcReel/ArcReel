"""文本生成服务层核心接口定义。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, Set


class TextCapability(str, Enum):
    """文本后端支持的能力枚举。"""
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"


class TextTaskType(str, Enum):
    """文本生成任务类型。"""
    SCRIPT = "script"
    OVERVIEW = "overview"
    STYLE_ANALYSIS = "style"


@dataclass
class ImageInput:
    """图片输入（用于 vision）。"""
    path: Path | None = None
    url: str | None = None


@dataclass
class TextGenerationRequest:
    """通用文本生成请求。各 Backend 忽略不支持的字段。"""
    prompt: str
    response_schema: dict | None = None
    images: list[ImageInput] | None = None
    system_prompt: str | None = None


@dataclass
class TextGenerationResult:
    """通用文本生成结果。"""
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class TextBackend(Protocol):
    """文本生成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> Set[TextCapability]: ...

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult: ...
