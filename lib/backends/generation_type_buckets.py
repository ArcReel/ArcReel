"""任务类型桶（t2i / i2i / i2v / r2v）归属判定 —— 把既有能力声明翻译成桶，不新增第二份声明。

判定来源逐桶固定：

- 图片：生效能力（``lib.backends.image_capabilities``），即模型条目的文生图 / 图生图（自定义供应商的能力覆盖
  整维替换；ComfyUI 端点不看条目）与调用通道声明（内置 backend 的 ``CHANNEL_CAPABILITIES``、自定义供应商
  endpoint spec 的 ``image_capabilities``）都满足的部分，与执行层 ``MediaGenerator`` 的能力校验同一个结果；
- 内置视频：backend ``VideoCapabilities``，i2v 取 ``first_frame``、r2v 取 ``max_reference_images``；
- 自定义供应商视频：endpoint 系统判定 ⊕ 模型级覆盖的既有合成点，同样取 ``first_frame`` 与
  ``max_reference_images``。

内置视频两个维度取 backend 而非模型目录：backend 的能力声明与请求构造同源
（例如 ``vidu`` 按端点白名单算 caps，白名单外的 model 提交首帧会在构造请求时报错），registry
不声明这两维。桶承诺的是「选中的组合执行得了」，故以执行期同源的那一份为准。视频两维的判定式本身由
``lib.config.resolver.video_capability_satisfied`` 提供，与解析层的能力闸共用同一份，两处不会漂。

判定不出（endpoint 已下线、backend 未声明能力函数）时返回空集而非全集：候选列表宁缺勿滥，
配进去的组合执行期一样必败，不如不出现在下拉里。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_capabilities import (
    builtin_channel_capabilities,
    custom_image_model_capabilities,
    effective_image_capabilities,
    intersect_image_capabilities,
)
from lib.catalog.query import CatalogModel
from lib.config.resolver import video_capability_satisfied
from lib.custom_provider.capabilities import synthesize_video_capabilities
from lib.custom_provider.endpoints import EndpointSpec, endpoint_to_image_capabilities, endpoint_to_media_type

GenerationTypeBucket = Literal["t2i", "i2i", "i2v", "r2v"]

#: 每个 media_type 有哪些桶。文本 / 音频不设桶，故不在表内。
BUCKETS_BY_MEDIA_TYPE: dict[str, tuple[GenerationTypeBucket, ...]] = {
    "image": ("t2i", "i2i"),
    "video": ("i2v", "r2v"),
}


def _image_buckets(caps: Iterable[ImageCapability]) -> frozenset[GenerationTypeBucket]:
    caps = frozenset(caps)
    buckets: set[GenerationTypeBucket] = set()
    if ImageCapability.TEXT_TO_IMAGE in caps:
        buckets.add("t2i")
    if ImageCapability.IMAGE_TO_IMAGE in caps:
        buckets.add("i2i")
    return frozenset(buckets)


def _video_buckets(has_i2v: bool, max_reference_images: int) -> frozenset[GenerationTypeBucket]:
    return frozenset(
        generation_type
        for generation_type in ("i2v", "r2v")
        if video_capability_satisfied(
            generation_type=generation_type, first_frame=has_i2v, max_reference_images=max_reference_images
        )
    )


def builtin_model_buckets(provider_id: str, model_id: str, model_info: CatalogModel) -> frozenset[GenerationTypeBucket]:
    """内置模型具备的任务类型桶；文本 / 音频模型恒为空集。"""
    if model_info.media_type == "image":
        try:
            channel = builtin_channel_capabilities(provider_id)
        except ValueError:
            return frozenset()
        return _image_buckets(effective_image_capabilities(channel, model_id=model_id, builtin_provider=provider_id))
    if model_info.media_type != "video":
        return frozenset()

    try:
        caps = builtin_video_capabilities_for_model(provider_id, model_id)
    except ValueError:
        return frozenset()
    return _video_buckets(caps.first_frame, caps.max_reference_images)


def custom_model_buckets(
    *,
    endpoint: str,
    model_id: str,
    capability_overrides: object | None = None,
    endpoint_spec: EndpointSpec | None = None,
) -> frozenset[GenerationTypeBucket]:
    """自定义供应商模型具备的任务类型桶；文本 / 音频 endpoint 与未知 endpoint 恒为空集。"""
    try:
        media_type = endpoint_spec.media_type if endpoint_spec is not None else endpoint_to_media_type(endpoint)
    except ValueError:
        return frozenset()

    if media_type == "image":
        caps = (
            endpoint_spec.image_capabilities if endpoint_spec is not None else endpoint_to_image_capabilities(endpoint)
        )
        if caps is None:
            return frozenset()
        endpoint_kind = endpoint_spec.kind if endpoint_spec is not None else "python"
        model_caps = custom_image_model_capabilities(
            model_id, overrides=capability_overrides, endpoint_kind=endpoint_kind
        )
        return _image_buckets(intersect_image_capabilities(caps, model_caps))
    if media_type != "video":
        return frozenset()

    try:
        video_caps = synthesize_video_capabilities(
            endpoint=endpoint,
            model_id=model_id,
            overrides=capability_overrides,
            endpoint_spec=endpoint_spec,
        )
    except ValueError:
        return frozenset()
    return _video_buckets(video_caps.first_frame, video_caps.max_reference_images)
