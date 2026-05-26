"""Kling Omni 专用请求类型。

这些类型只描述 Omni 原生多模态请求结构，不负责 HTTP 组装。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class KlingOmniFrameType(StrEnum):
    """Omni 图片帧类型。"""

    FIRST_FRAME = "first_frame"
    END_FRAME = "end_frame"


class KlingOmniShotType(StrEnum):
    """Omni 多镜头分镜方式。"""

    CUSTOMIZE = "customize"
    INTELLIGENCE = "intelligence"


class KlingOmniVideoReferType(StrEnum):
    """Omni 视频参考类型。"""

    FEATURE = "feature"
    BASE = "base"


class KlingOmniMode(StrEnum):
    """Omni 生成模式。"""

    STD = "std"
    PRO = "pro"
    FOUR_K = "4k"


class KlingOmniSoundMode(StrEnum):
    """Omni 声音生成开关。"""

    ON = "on"
    OFF = "off"


@dataclass(frozen=True)
class KlingOmniImageInput:
    """Omni 图片输入，可为本地路径或远端 URL。"""

    image_path: Path | None = None
    image_url: str | None = None
    frame_type: KlingOmniFrameType | None = None

    def __post_init__(self) -> None:
        has_path = self.image_path is not None
        has_url = bool(self.image_url)
        if has_path == has_url:
            raise ValueError("KlingOmniImageInput 必须且只能提供 image_path 或 image_url 其中之一")


@dataclass(frozen=True)
class KlingOmniElementInput:
    """Omni 主体库输入。"""

    element_id: int

    def __post_init__(self) -> None:
        if self.element_id <= 0:
            raise ValueError("KlingOmniElementInput.element_id 必须大于 0")


@dataclass(frozen=True)
class KlingOmniVideoInput:
    """Omni 视频输入，可为本地路径或远端 URL。"""

    video_path: Path | None = None
    video_url: str | None = None
    refer_type: KlingOmniVideoReferType = KlingOmniVideoReferType.BASE
    keep_original_sound: bool = True

    def __post_init__(self) -> None:
        has_path = self.video_path is not None
        has_url = bool(self.video_url)
        if has_path == has_url:
            raise ValueError("KlingOmniVideoInput 必须且只能提供 video_path 或 video_url 其中之一")


@dataclass(frozen=True)
class KlingOmniShot:
    """Omni 自定义分镜。"""

    index: int
    prompt: str
    duration_seconds: int

    def __post_init__(self) -> None:
        if self.index <= 0:
            raise ValueError("KlingOmniShot.index 必须大于 0")
        if not self.prompt.strip():
            raise ValueError("KlingOmniShot.prompt 不能为空")
        if self.duration_seconds <= 0:
            raise ValueError("KlingOmniShot.duration_seconds 必须大于 0")


@dataclass(frozen=True)
class KlingOmniRequestOptions:
    """Omni 原生请求附加参数。"""

    images: tuple[KlingOmniImageInput, ...] = ()
    elements: tuple[KlingOmniElementInput, ...] = ()
    videos: tuple[KlingOmniVideoInput, ...] = ()
    multi_shot: bool = False
    shot_type: KlingOmniShotType | None = None
    shots: tuple[KlingOmniShot, ...] = ()
    mode: KlingOmniMode = KlingOmniMode.PRO
    sound: KlingOmniSoundMode = KlingOmniSoundMode.OFF
    watermark_enabled: bool = False
    callback_url: str | None = None
    external_task_id: str | None = None

    def __post_init__(self) -> None:
        if self.multi_shot:
            if self.shot_type is None:
                raise ValueError("KlingOmniRequestOptions.multi_shot=True 时必须提供 shot_type")
            if self.shot_type == KlingOmniShotType.CUSTOMIZE and not self.shots:
                raise ValueError("KlingOmniRequestOptions.shot_type=customize 时必须提供 shots")
            if self.shot_type != KlingOmniShotType.CUSTOMIZE and self.shots:
                raise ValueError("仅在 shot_type=customize 时允许提供 shots")
        else:
            if self.shot_type is not None:
                raise ValueError("KlingOmniRequestOptions.multi_shot=False 时不能提供 shot_type")
            if self.shots:
                raise ValueError("KlingOmniRequestOptions.multi_shot=False 时不能提供 shots")