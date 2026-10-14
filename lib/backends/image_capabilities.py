"""图片的生效能力：模型条目与调用通道都满足的部分（docs/adr/0104）。

文生图与图生图在两侧各是一个维度：模型条目说模型接不接受（模型事实），调用通道说这条通道实现了没有
（通道事实，内置调用通道见 ``CHANNEL_CAPABILITIES``，自定义供应商见端点声明）。任务类型桶的候选与执行时
的校验都取这里的结果。

模型事实按供应商模型对应的条目取：内置供应商取清单所引的条目，自定义供应商按模型名现查（docs/adr/0105）。
对应不上条目时模型事实不加约束，生效能力就是通道事实，超出模型能力的请求由上游拒绝。自定义供应商的模型
可以用能力覆盖整维替换文生图或图生图（见 :mod:`lib.custom_provider.model_facts`），替换的是模型事实这一侧，
通道没实现的能力覆盖也打不开（写入侧拒绝）。

ComfyUI 端点的能力只从节点绑定推导（docs/adr/0082）：模型行的名字由用户随手填，与 workflow 能做什么无关，
不按名字对应条目，也不接受能力覆盖，生效能力就是端点声明。
"""

from __future__ import annotations

from collections.abc import Iterable

from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_backends.registry import channel_capabilities
from lib.catalog import query as catalog
from lib.custom_provider.model_facts import image_model_facts


def builtin_channel_capabilities(provider_id: str) -> frozenset[ImageCapability]:
    """内置供应商的图片调用通道实现了文生图还是图生图（通道事实）；供应商没有图片调用通道时抛 ``ValueError``。"""
    return channel_capabilities(get_provider_spec(provider_id, "image").registry_backend)


def image_model_capabilities(
    model_id: str, *, builtin_provider: str | None, overrides: object | None = None
) -> frozenset[ImageCapability] | None:
    """模型事实一侧允许的文生图 / 图生图；两维都不加约束时为 ``None``。

    ``builtin_provider`` 是内置供应商 id，按清单取条目；自定义供应商的模型传 ``None``，按模型名现查，
    ``overrides`` 是它的能力覆盖（整维替换条目的值）。不加约束的那一维按允许算。
    """
    if builtin_provider is not None:
        model = catalog.get_model(builtin_provider, model_id, media_type="image")
        if model is None:
            return None
        return frozenset(cap for cap in ImageCapability if cap.value in model.capabilities)
    facts = image_model_facts(model_id, overrides)
    declared = {
        ImageCapability.TEXT_TO_IMAGE: facts.text_to_image,
        ImageCapability.IMAGE_TO_IMAGE: facts.image_to_image,
    }
    if all(value is None for value in declared.values()):
        return None
    return frozenset(cap for cap, supported in declared.items() if supported is not False)


def custom_image_model_capabilities(
    model_id: str, *, overrides: object | None, endpoint_kind: str
) -> frozenset[ImageCapability] | None:
    """自定义供应商图片模型的模型事实一侧；ComfyUI 端点只看节点绑定，恒为 ``None``（不加约束）。"""
    if endpoint_kind == COMFYUI_KIND:
        return None
    return image_model_capabilities(model_id, builtin_provider=None, overrides=overrides)


def intersect_image_capabilities(
    channel: Iterable[ImageCapability], model: frozenset[ImageCapability] | None
) -> frozenset[ImageCapability]:
    """调用通道实现了、且模型事实一侧允许的部分；``model`` 为 ``None`` 时就是通道事实。"""
    implemented = frozenset(channel)
    return implemented if model is None else implemented & model


def effective_image_capabilities(
    channel: Iterable[ImageCapability], *, model_id: str, builtin_provider: str | None
) -> frozenset[ImageCapability]:
    """调用通道实现了、且模型条目接受的文生图 / 图生图。参数含义同 :func:`image_model_capabilities`。"""
    return intersect_image_capabilities(channel, image_model_capabilities(model_id, builtin_provider=builtin_provider))
