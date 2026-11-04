"""任务类型桶（t2i / i2i / i2v / r2v）归属判定 —— 把既有能力声明翻译成桶，不新增第二份声明。

判定来源逐桶固定：

- 图片：生效能力（``lib.backends.image_capabilities``），即模型条目的文生图 / 图生图（自定义供应商的能力覆盖
  整维替换；ComfyUI 端点不看条目）与调用通道声明（内置 backend 的 ``CHANNEL_CAPABILITIES``、自定义供应商
  endpoint spec 的 ``image_capabilities``）都满足的部分，与执行层 ``MediaGenerator`` 的能力校验同一个结果；
  图生图另须参考图上限的生效值接受参考图（条目显式记为 0 张时不进 i2i 桶）；
- 视频：生效能力（``lib.backends.video_capabilities``），即调用通道声明（内置 backend 的 ``VideoCapabilities``、
  自定义供应商 endpoint 的系统判定）与模型条目两侧求值、再叠加能力覆盖的结果，i2v 取 ``first_frame``、r2v 取
  参考图上限的生效值（未收录或大于 0 即接受；条目显式记为 0 张时不进 r2v 桶）。ComfyUI 端点不看条目。

桶承诺的是「选中的组合执行得了」，故与执行闸门读同一个求值结果。视频两维的判定式本身由
``lib.config.resolver.video_capability_satisfied`` 提供，与解析层的能力闸共用同一份，两处不会漂。

判定不出（endpoint 已下线、backend 未声明能力函数）时返回空集而非全集：候选列表宁缺勿滥，
配进去的组合执行期一样必败，不如不出现在下拉里。

不进某个桶的模型带着原因（:class:`BucketFit`）：不满足的能力维度，取自进桶判定的同一个求值结果，维度键与能力
接口（``lib.custom_provider.capability_view``）同名。候选接口据此告诉用户为什么在这一处找不到它。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_capabilities import (
    builtin_channel_capabilities,
    builtin_channel_reference_limit,
    custom_image_model_capabilities,
    effective_image_capabilities,
    entry_reference_limit,
    image_reference_limit,
    intersect_image_capabilities,
)
from lib.backends.input_limits import LimitState
from lib.catalog.query import CatalogModel
from lib.config.resolver import video_capability_satisfied
from lib.custom_provider.capabilities import synthesize_video_capabilities
from lib.custom_provider.endpoints import (
    EndpointSpec,
    endpoint_reference_image_limit,
    endpoint_to_image_capabilities,
    endpoint_to_media_type,
    get_endpoint_spec,
)

GenerationTypeBucket = Literal["t2i", "i2i", "i2v", "r2v"]

#: 每个 media_type 有哪些桶。文本 / 音频不设桶，故不在表内。
BUCKETS_BY_MEDIA_TYPE: dict[str, tuple[GenerationTypeBucket, ...]] = {
    "image": ("t2i", "i2i"),
    "video": ("i2v", "r2v"),
}


#: 进桶判定看的能力维度，键与能力接口的维度同名。``max_reference_images`` 在图片的图生图里指参考图上限记为 0 张
#: （图生图本身走得通），在视频的参考生视频里指参考图上限不接受参考图（通道没实现或条目记为 0 张）。
BucketDimension = Literal["text_to_image", "image_to_image", "max_reference_images", "first_frame"]


@dataclass(frozen=True)
class BucketFit:
    """一个模型对所在媒体类型各任务类型桶的判定。

    ``unmet`` 只收不进的桶：桶 → 不满足的能力维度。维度为空元组表示判定不出（endpoint 已下线、backend 未声明
    能力函数），这类模型同样不进桶。文本 / 音频模型没有桶，``unmet`` 为空。
    """

    media_type: str
    unmet: Mapping[GenerationTypeBucket, tuple[BucketDimension, ...]] = field(default_factory=dict)

    @property
    def buckets(self) -> frozenset[GenerationTypeBucket]:
        return frozenset(b for b in BUCKETS_BY_MEDIA_TYPE.get(self.media_type, ()) if b not in self.unmet)

    @classmethod
    def undetermined(cls, media_type: str) -> BucketFit:
        return cls(media_type, dict.fromkeys(BUCKETS_BY_MEDIA_TYPE.get(media_type, ()), ()))


def _image_fit(caps: Iterable[ImageCapability], *, entry_limit: int | None, channel_limit: int | None) -> BucketFit:
    caps = frozenset(caps)
    unmet: dict[GenerationTypeBucket, tuple[BucketDimension, ...]] = {}
    if ImageCapability.TEXT_TO_IMAGE not in caps:
        unmet["t2i"] = ("text_to_image",)
    reference = image_reference_limit(capabilities=caps, entry_limit=entry_limit, channel_limit=channel_limit)
    if not reference.accepts_input:
        unmet["i2i"] = ("image_to_image",) if reference.state == LimitState.UNSUPPORTED else ("max_reference_images",)
    return BucketFit("image", unmet)


#: 视频各桶唯一看的那一维，与 ``video_capability_satisfied`` 的判定式一一对应。
_VIDEO_BUCKET_DIMENSION: dict[GenerationTypeBucket, BucketDimension] = {
    "i2v": "first_frame",
    "r2v": "max_reference_images",
}


def _video_fit(has_i2v: bool, max_reference_images: int | None) -> BucketFit:
    return BucketFit(
        "video",
        {
            generation_type: (_VIDEO_BUCKET_DIMENSION[generation_type],)
            for generation_type in ("i2v", "r2v")
            if not video_capability_satisfied(
                generation_type=generation_type, first_frame=has_i2v, max_reference_images=max_reference_images
            )
        },
    )


def builtin_model_buckets(provider_id: str, model_id: str, model_info: CatalogModel) -> frozenset[GenerationTypeBucket]:
    """内置模型具备的任务类型桶；文本 / 音频模型恒为空集。"""
    return builtin_model_bucket_fit(provider_id, model_id, model_info).buckets


def builtin_model_bucket_fit(provider_id: str, model_id: str, model_info: CatalogModel) -> BucketFit:
    """内置模型对各任务类型桶的判定，连同不进桶的原因。"""
    if model_info.media_type == "image":
        try:
            channel = builtin_channel_capabilities(provider_id)
            channel_limit = builtin_channel_reference_limit(provider_id)
        except ValueError:
            return BucketFit.undetermined("image")
        return _image_fit(
            effective_image_capabilities(channel, model_id=model_id, builtin_provider=provider_id),
            entry_limit=model_info.max_reference_images,
            channel_limit=channel_limit,
        )
    if model_info.media_type != "video":
        return BucketFit(model_info.media_type)

    try:
        caps = builtin_video_capabilities_for_model(provider_id, model_id)
    except ValueError:
        return BucketFit.undetermined("video")
    return _video_fit(caps.first_frame, caps.max_reference_images)


def custom_model_buckets(
    *,
    endpoint: str,
    model_id: str,
    capability_overrides: object | None = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> frozenset[GenerationTypeBucket]:
    """自定义供应商模型具备的任务类型桶；文本 / 音频 endpoint 与未知 endpoint 恒为空集。"""
    return custom_model_bucket_fit(
        endpoint=endpoint,
        model_id=model_id,
        capability_overrides=capability_overrides,
        endpoint_spec=endpoint_spec,
        entry_binding=entry_binding,
    ).buckets


def custom_model_bucket_fit(
    *,
    endpoint: str,
    model_id: str,
    capability_overrides: object | None = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> BucketFit:
    """自定义供应商模型对各任务类型桶的判定，连同不进桶的原因；未知 endpoint 没有媒体类型，也就没有桶。"""
    try:
        media_type = endpoint_spec.media_type if endpoint_spec is not None else endpoint_to_media_type(endpoint)
    except ValueError:
        return BucketFit("")

    if media_type == "image":
        caps = (
            endpoint_spec.image_capabilities if endpoint_spec is not None else endpoint_to_image_capabilities(endpoint)
        )
        if caps is None:
            return BucketFit.undetermined("image")
        spec = endpoint_spec or get_endpoint_spec(endpoint)
        model_caps = custom_image_model_capabilities(
            model_id, overrides=capability_overrides, endpoint_kind=spec.kind, entry_binding=entry_binding
        )
        return _image_fit(
            intersect_image_capabilities(caps, model_caps),
            entry_limit=entry_reference_limit(
                model_id, builtin_provider=None, endpoint_kind=spec.kind, entry_binding=entry_binding
            ),
            channel_limit=endpoint_reference_image_limit(spec),
        )
    if media_type != "video":
        return BucketFit(media_type)

    try:
        video_caps = synthesize_video_capabilities(
            endpoint=endpoint,
            model_id=model_id,
            overrides=capability_overrides,
            endpoint_spec=endpoint_spec,
            entry_binding=entry_binding,
        )
    except ValueError:
        return BucketFit.undetermined("video")
    return _video_fit(video_caps.first_frame, video_caps.max_reference_images)
