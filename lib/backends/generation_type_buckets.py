"""任务类型桶（t2i / i2i / i2v / r2v）归属判定 —— 把既有能力声明翻译成桶，不新增第二份声明。

判定来源逐桶固定：

- 图片：生效能力（``lib.backends.image_capabilities``），即模型条目的文生图 / 图生图（自定义供应商的能力覆盖
  整维替换；ComfyUI 端点不看条目）与调用通道声明（内置 backend 的 ``CHANNEL_CAPABILITIES``、自定义供应商
  endpoint spec 的 ``image_capabilities``）都满足的部分，与执行层 ``MediaGenerator`` 的能力校验同一个结果；
  图生图另须参考图上限的生效值接受参考图（条目显式记为 0 张时不进 i2i 桶）；
- 视频：生效能力（``lib.backends.video_capabilities``），即调用通道声明（内置 backend 的 ``VideoCapabilities``、
  自定义供应商 endpoint 的系统判定）与模型条目两侧求值、再叠加能力覆盖的结果，i2v 取 ``first_frame``、r2v 取
  参考图上限的生效值（未收录或大于 0 即接受；条目显式记为 0 张时不进 r2v 桶）。ComfyUI 端点不看条目。

视频候选与解析闸共用纯能力内核的桶判定。

判定不出（endpoint 已下线、backend 未声明能力函数）时返回空集而非全集：候选列表宁缺勿滥，
配进去的组合执行期一样必败，不如不出现在下拉里。

不进某个桶的模型带着原因（:class:`BucketFit`）：不满足的能力维度，取自进桶判定的同一个求值结果，维度键与能力
接口（``lib.custom_provider.capability_view``）同名。候选接口据此告诉用户为什么在这一处找不到它。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from lib.backends.backend_assembly.specs import builtin_video_snapshot
from lib.backends.image_capabilities import builtin_image_snapshot
from lib.capabilities.kernel import CapabilitySnapshot, bucket_fit
from lib.catalog.query import CatalogModel
from lib.custom_provider.capabilities import custom_image_snapshot, custom_video_snapshot
from lib.custom_provider.endpoints import (
    EndpointSpec,
    endpoint_to_media_type,
)

GenerationTypeBucket = Literal["t2i", "i2i", "i2v", "r2v"]

BUCKETS_BY_MEDIA_TYPE: dict[str, tuple[GenerationTypeBucket, ...]] = {
    "image": ("t2i", "i2i"),
    "video": ("i2v", "r2v"),
}


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


def _image_fit(snapshot: CapabilitySnapshot | None) -> BucketFit:
    return BucketFit(
        "image", cast(dict[GenerationTypeBucket, tuple[BucketDimension, ...]], bucket_fit("image", snapshot))
    )


def _video_fit(snapshot: CapabilitySnapshot | None) -> BucketFit:
    return BucketFit(
        "video", cast(dict[GenerationTypeBucket, tuple[BucketDimension, ...]], bucket_fit("video", snapshot))
    )


def builtin_model_buckets(provider_id: str, model_id: str, model_info: CatalogModel) -> frozenset[GenerationTypeBucket]:
    """内置模型具备的任务类型桶；文本 / 音频模型恒为空集。"""
    return builtin_model_bucket_fit(provider_id, model_id, model_info).buckets


def builtin_model_bucket_fit(provider_id: str, model_id: str, model_info: CatalogModel) -> BucketFit:
    """内置模型对各任务类型桶的判定，连同不进桶的原因。"""
    if model_info.media_type == "image":
        try:
            image_caps = builtin_image_snapshot(provider_id, model_id, base_model=model_info)
        except ValueError:
            return _image_fit(None)
        return _image_fit(image_caps)
    if model_info.media_type != "video":
        return BucketFit(model_info.media_type)

    try:
        caps = builtin_video_snapshot(provider_id, model_id)
    except ValueError:
        return _video_fit(None)
    return _video_fit(caps)


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
        try:
            image_caps = custom_image_snapshot(
                endpoint=endpoint,
                model_id=model_id,
                overrides=capability_overrides,
                endpoint_spec=endpoint_spec,
                entry_binding=entry_binding,
            )
        except ValueError:
            return _image_fit(None)
        return _image_fit(image_caps)
    if media_type != "video":
        return BucketFit(media_type)

    try:
        video_caps = custom_video_snapshot(
            endpoint=endpoint,
            model_id=model_id,
            overrides=capability_overrides,
            endpoint_spec=endpoint_spec,
            entry_binding=entry_binding,
        )
    except ValueError:
        return _video_fit(None)
    return _video_fit(video_caps)
