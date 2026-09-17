"""节点绑定的语义键名录：推断、校验与运行时填值共读这一份。

语义键与声明式端点共用命名（``prompt`` / ``start_image`` / ``reference_images`` …），这样同一个
维度在两种 ``kind`` 上叫同一个名字。哪些键属于哪种媒体类型是名录里的事实，不是各处各写一份
``if media_type == "image"``——白名单漂移会让保存期放行的绑定在填值期落空。
"""

from __future__ import annotations

#: 视频端点可用的全部语义键，也是 schema 里 ``bindings`` 的封闭键集。
VIDEO_BINDING_KEYS = (
    "prompt",
    "negative_prompt",
    "start_image",
    "end_image",
    "reference_images",
    "width",
    "height",
    "frames",
    "fps",
    "seed",
    "output",
)

#: 图像端点可用的语义键：没有首尾帧，也没有帧数与帧率这两个时间轴维度。
IMAGE_BINDING_KEYS = (
    "prompt",
    "negative_prompt",
    "reference_images",
    "width",
    "height",
    "seed",
    "output",
)

#: ``media_type`` → 该媒体类型允许的语义键。
BINDING_KEYS_BY_MEDIA_TYPE: dict[str, frozenset[str]] = {
    "image": frozenset(IMAGE_BINDING_KEYS),
    "video": frozenset(VIDEO_BINDING_KEYS),
}

#: 两种媒体类型都必须绑定的语义键：没有提示词无从下笔，没有产物取不到成片。
REQUIRED_BINDING_KEYS = ("prompt", "output")
