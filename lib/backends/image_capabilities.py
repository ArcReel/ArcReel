"""图片的生效能力：模型条目与调用通道都满足的部分（docs/adr/0104）。

文生图与图生图在两侧各是一个维度：模型条目说模型接不接受（模型事实），调用通道说这条通道实现了没有
（通道事实，内置调用通道见 ``CHANNEL_CAPABILITIES``，自定义供应商见端点声明）。任务类型桶的候选与执行时
的校验都取这里的结果。

模型事实按供应商模型对应的条目取：内置供应商取清单所引的条目，自定义供应商按模型名现查（docs/adr/0105）。
对应不上条目时模型事实不加约束，生效能力就是通道事实，超出模型能力的请求由上游拒绝。
"""

from __future__ import annotations

from collections.abc import Iterable

from arcreel_catalog_core import ImageFacts
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_backends.registry import channel_capabilities
from lib.catalog import query as catalog


def builtin_channel_capabilities(provider_id: str) -> frozenset[ImageCapability]:
    """内置供应商的图片调用通道实现了文生图还是图生图（通道事实）；供应商没有图片调用通道时抛 ``ValueError``。"""
    return channel_capabilities(get_provider_spec(provider_id, "image").registry_backend)


def image_model_capabilities(model_id: str, *, builtin_provider: str | None) -> frozenset[ImageCapability] | None:
    """条目上的图片模型事实；对应不上条目时为 ``None``（不加约束）。

    ``builtin_provider`` 是内置供应商 id，按清单取条目；自定义供应商的模型传 ``None``，按模型名现查。
    """
    if builtin_provider is not None:
        model = catalog.get_model(builtin_provider, model_id, media_type="image")
        if model is None:
            return None
        return frozenset(cap for cap in ImageCapability if cap.value in model.capabilities)
    entry = catalog.find_entry(model_id, media_type="image")
    if entry is None or not isinstance(entry.facts, ImageFacts):
        return None
    facts = entry.facts
    declared = {
        ImageCapability.TEXT_TO_IMAGE: facts.text_to_image,
        ImageCapability.IMAGE_TO_IMAGE: facts.image_to_image,
    }
    return frozenset(cap for cap, supported in declared.items() if supported)


def effective_image_capabilities(
    channel: Iterable[ImageCapability], *, model_id: str, builtin_provider: str | None
) -> frozenset[ImageCapability]:
    """调用通道实现了、且模型条目接受的文生图 / 图生图。参数含义同 :func:`image_model_capabilities`。"""
    model = image_model_capabilities(model_id, builtin_provider=builtin_provider)
    implemented = frozenset(channel)
    return implemented if model is None else implemented & model
