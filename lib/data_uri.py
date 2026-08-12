"""出站请求侧的 base64 data URI 编码。

各供应商的图像/视频接口普遍接受 `data:<mime>;base64,<内容>` 形态的内联素材，走 data URI
可以免掉一层文件服务。编码动作本身各家一致，差异只在「哪些扩展名映射到哪个 MIME」——
表由各供应商的 `*_shared` 模块自己持有并传入，本模块不持有任何供应商口径。

与 `lib/image_utils.py` / `lib/audio_utils.py` 的分工：那两个模块守的是入站上传侧
（尺寸压缩、时长与格式校验），本模块只负责把已落盘的素材编成出站请求体里的字段。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path

DEFAULT_IMAGE_MIME = "image/png"


def file_to_data_uri(path: Path, mime: str) -> str:
    """本地文件 → base64 data URI；读不到时 OSError 向上冒泡由调用方决定语义。"""
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def image_to_data_uri(
    image_path: Path,
    mime_types: Mapping[str, str],
    *,
    default_mime: str = DEFAULT_IMAGE_MIME,
) -> str:
    """本地图片 → base64 data URI，按 `mime_types` 查扩展名，未登记时回落 `default_mime`。

    回落而非报错：图片扩展名的受理口径由上传侧把关，各家 `mime_types` 只列各自明确声明
    过的格式，落表外的按 png 送出与既有行为一致。
    """
    mime = mime_types.get(image_path.suffix.lower(), default_mime)
    return file_to_data_uri(image_path, mime)
