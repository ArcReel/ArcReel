"""读一份定义的媒体类型：索引投影、端点投影与镜像列共用这一份实现。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .kinds import COMFYUI_KIND, DECLARATIVE_KIND

#: 声明式定义不写 ``media_type`` 时的媒体类型。该字段晚于视频定义加入格式，已有定义与随版定义
#: 都不写它，缺省必须是视频。
DEFAULT_DECLARATIVE_MEDIA_TYPE = "video"

#: ``kind`` → 从定义读媒体类型。两种 kind 都由定义自己声明，ComfyUI 定义必须写。
_MEDIA_TYPE_BY_KIND: Mapping[str, Callable[[Mapping[str, Any]], str]] = {
    DECLARATIVE_KIND: lambda definition: str(definition.get("media_type", DEFAULT_DECLARATIVE_MEDIA_TYPE)),
    COMFYUI_KIND: lambda definition: str(definition["media_type"]),
}


def definition_media_type(definition: Mapping[str, Any]) -> str:
    """读一份**已过校验**的定义的媒体类型，按 ``kind`` 取。

    Raises:
        KeyError: 定义缺 ``kind``，或 ComfyUI 定义缺 ``media_type``。
        ValueError: 定义的 ``kind`` 没有媒体类型读法。
    """
    kind = str(definition["kind"])
    read = _MEDIA_TYPE_BY_KIND.get(kind)
    if read is None:
        raise ValueError(f"unsupported endpoint definition kind: {kind!r}")
    return read(definition)
