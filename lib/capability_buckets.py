"""任务类型桶（t2i / i2i / i2v / r2v）归属判定 —— 把既有能力声明翻译成桶，不新增第二份声明。

判定来源逐桶固定：

- 内置图片：registry ``ModelInfo.capabilities`` 的 ``text_to_image`` / ``image_to_image``；
- 内置视频：backend ``VideoCapabilities``，i2v 取 ``first_frame``、r2v 取 ``max_reference_images``；
- 自定义供应商图片：endpoint spec 的 ``image_capabilities``；
- 自定义供应商视频：endpoint 系统判定 ⊕ 模型级覆盖的既有合成点，同样取 ``first_frame`` 与
  ``max_reference_images``。

内置视频两个维度取 backend 而非 registry ``ModelInfo``：backend 的能力声明与请求构造同源
（例如 ``vidu`` 按端点白名单算 caps，白名单外的 model 提交首帧会在构造请求时报错），registry
不声明这两维。桶承诺的是「选中的组合执行得了」，故以执行期同源的那一份为准。视频两维的判定式本身由
``lib.config.resolver.video_capability_satisfied`` 提供，与解析层的能力闸共用同一份，两处不会漂。

判定不出（endpoint 已下线、backend 未声明能力函数）时返回空集而非全集：候选列表宁缺勿滥，
配进去的组合执行期一样必败，不如不出现在下拉里。
"""

from __future__ import annotations

from typing import Literal

from lib.backend_assembly.specs import get_provider_spec
from lib.config.registry import ModelInfo
from lib.config.resolver import video_capability_satisfied
from lib.custom_provider.capabilities import synthesize_video_capabilities
from lib.custom_provider.endpoints import EndpointSpec, endpoint_to_image_capabilities, endpoint_to_media_type
from lib.image_backends.base import ImageCapability
from lib.video_backends.registry import video_capabilities_for_model as builtin_video_capabilities_for_model

CapabilityBucket = Literal["t2i", "i2i", "i2v", "r2v"]

#: 每个 media_type 有哪些桶。文本 / 音频不设桶，故不在表内。
BUCKETS_BY_MEDIA_TYPE: dict[str, tuple[CapabilityBucket, ...]] = {
    "image": ("t2i", "i2i"),
    "video": ("i2v", "r2v"),
}


def _image_buckets_from_capabilities(has_t2i: bool, has_i2i: bool) -> frozenset[CapabilityBucket]:
    buckets: set[CapabilityBucket] = set()
    if has_t2i:
        buckets.add("t2i")
    if has_i2i:
        buckets.add("i2i")
    return frozenset(buckets)


def _video_buckets(has_i2v: bool, max_reference_images: int) -> frozenset[CapabilityBucket]:
    return frozenset(
        cap
        for cap in ("i2v", "r2v")
        if video_capability_satisfied(capability=cap, first_frame=has_i2v, max_reference_images=max_reference_images)
    )


def builtin_model_buckets(provider_id: str, model_id: str, model_info: ModelInfo) -> frozenset[CapabilityBucket]:
    """内置模型具备的任务类型桶；文本 / 音频模型恒为空集。"""
    if model_info.media_type == "image":
        return _image_buckets_from_capabilities(
            "text_to_image" in model_info.capabilities,
            "image_to_image" in model_info.capabilities,
        )
    if model_info.media_type != "video":
        return frozenset()

    try:
        spec = get_provider_spec(provider_id, "video")
        caps = builtin_video_capabilities_for_model(spec.registry_backend, model_id)
    except ValueError:
        return frozenset()
    return _video_buckets(caps.first_frame, caps.max_reference_images)


def custom_model_buckets(
    *,
    endpoint: str,
    model_id: str,
    capability_overrides: object | None = None,
    endpoint_spec: EndpointSpec | None = None,
) -> frozenset[CapabilityBucket]:
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
        return _image_buckets_from_capabilities(
            ImageCapability.TEXT_TO_IMAGE in caps,
            ImageCapability.IMAGE_TO_IMAGE in caps,
        )
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
