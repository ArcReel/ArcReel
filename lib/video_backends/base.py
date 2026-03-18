"""视频生成服务层核心接口定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, Set


PROVIDER_GEMINI = "gemini"
PROVIDER_SEEDANCE = "seedance"
PROVIDER_GROK = "grok"


class VideoCapability(str, Enum):
    """视频后端支持的能力枚举。"""
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    GENERATE_AUDIO = "generate_audio"
    NEGATIVE_PROMPT = "negative_prompt"
    VIDEO_EXTEND = "video_extend"
    SEED_CONTROL = "seed_control"
    FLEX_TIER = "flex_tier"


@dataclass
class VideoGenerationRequest:
    """通用视频生成请求。各 Backend 忽略不支持的字段。"""
    prompt: str
    output_path: Path
    aspect_ratio: str = "9:16"
    duration_seconds: int = 5
    resolution: str = "1080p"
    start_image: Optional[Path] = None
    generate_audio: bool = True

    # Veo 特有
    negative_prompt: Optional[str] = None

    # 项目上下文（用于构建文件服务 URL 等）
    project_name: Optional[str] = None

    # Seedance 特有
    service_tier: str = "default"
    seed: Optional[int] = None


@dataclass
class VideoGenerationResult:
    """通用视频生成结果。"""
    video_path: Path
    provider: str
    model: str
    duration_seconds: int

    video_uri: Optional[str] = None
    seed: Optional[int] = None
    usage_tokens: Optional[int] = None
    task_id: Optional[str] = None


class VideoBackend(Protocol):
    """视频生成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> Set[VideoCapability]: ...

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult: ...
