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

参考图上限同样两侧求值（:func:`image_reference_limit`）：条目记录的上限与调用通道的上限取较小值；图生图不在
生效能力里时为不支持；两侧都没有声明时为未收录，不裁剪、超出由上游拒绝。能力覆盖在两侧求值后整维替换上限，0 表示不接受参考图；
图生图的路径仍须调用通道实现。任务类型桶的候选、执行时的校验、编排层的裁剪与设置页都读这一个结果。
ComfyUI 端点的上限只来自节点绑定的参考图槽位数，不读条目。

画质同样两侧求值（:func:`image_quality_options`）：条目记模型接受哪些画质取值（``facts.qualities``），调用通道声明
是否接受显式画质；接受时有效可选值就是条目的取值，否则为空。不接受能力覆盖。设置接口读有效可选值，执行与预估都经
:func:`effective_image_quality` 把项目存值换成生效的画质，为空时调用通道沿用按分辨率档位推出 ``quality`` 的既有做法。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from arcreel_catalog_core import ImageFacts
from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from lib.backends.backend_assembly.specs import builtin_model_spec, get_provider_spec
from lib.backends.image_backends.base import ImageCapability, ImageCapabilityError
from lib.backends.image_backends.registry import (
    channel_accepts_explicit_quality,
    channel_capabilities,
    channel_reference_limit,
)
from lib.backends.input_limits import InputLimit, effective_input_limit
from lib.catalog import query as catalog
from lib.custom_provider import is_custom_provider
from lib.custom_provider.model_facts import image_model_facts, model_entry, model_fact_overrides

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry
    from lib.backends.backend_assembly.specs import ProviderSpec
    from lib.backends.image_backends.base import ImageBackend
    from lib.custom_provider.endpoints import EndpointSpec


def _builtin_image_spec(provider_id: str, model_id: str, endpoint: str | None) -> ProviderSpec:
    if endpoint is not None:
        return get_provider_spec(provider_id, "image", endpoint)
    return builtin_model_spec(provider_id, "image", model_id)


def builtin_channel_capabilities(
    provider_id: str, model_id: str, *, endpoint: str | None = None
) -> frozenset[ImageCapability]:
    """内置供应商模型挂接的图片调用端点实现了文生图还是图生图（通道事实）；供应商没有图片调用通道时抛 ``ValueError``。

    ``endpoint`` 给出时按它求值，供保存前核对换端点后的能力覆盖。
    """
    return channel_capabilities(_builtin_image_spec(provider_id, model_id, endpoint).registry_backend)


def builtin_channel_reference_limit(provider_id: str, model_id: str) -> int | None:
    """内置供应商模型挂接的图片调用端点静态声明的参考图上限（通道事实），``None`` 为通道不限张数。

    供应商没有图片调用通道时抛 ``ValueError``。
    """
    return channel_reference_limit(builtin_model_spec(provider_id, "image", model_id).registry_backend)


def image_model_capabilities(
    model_id: str,
    *,
    builtin_provider: str | None,
    overrides: object | None = None,
    entry_binding: str | None = None,
) -> frozenset[ImageCapability] | None:
    """模型事实一侧允许的文生图 / 图生图；两维都不加约束时为 ``None``。

    ``builtin_provider`` 是内置供应商 id，按清单取条目；自定义供应商的模型传 ``None``，按模型名现查，
    ``overrides`` 是它的能力覆盖（整维替换条目的值），``entry_binding`` 是它的条目绑定。不加约束的那一维按允许算。
    """
    if builtin_provider is not None:
        model = catalog.get_model(builtin_provider, model_id, media_type="image")
        if model is None:
            return None
        return frozenset(cap for cap in ImageCapability if cap.value in model.capabilities)
    facts = image_model_facts(model_id, overrides, entry_binding=entry_binding)
    declared = {
        ImageCapability.TEXT_TO_IMAGE: facts.text_to_image,
        ImageCapability.IMAGE_TO_IMAGE: facts.image_to_image,
    }
    if all(value is None for value in declared.values()):
        return None
    return frozenset(cap for cap, supported in declared.items() if supported is not False)


def custom_image_model_capabilities(
    model_id: str, *, overrides: object | None, endpoint_kind: str, entry_binding: str | None = None
) -> frozenset[ImageCapability] | None:
    """自定义供应商图片模型的模型事实一侧；ComfyUI 端点只看节点绑定，恒为 ``None``（不加约束）。"""
    if endpoint_kind == COMFYUI_KIND:
        return None
    return image_model_capabilities(model_id, builtin_provider=None, overrides=overrides, entry_binding=entry_binding)


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


def entry_reference_limit(
    model_id: str, *, builtin_provider: str | None, endpoint_kind: str = "python", entry_binding: str | None = None
) -> int | None:
    """条目记录的参考图上限（模型事实一侧）；未收录、对应不上条目或 ComfyUI 端点为 ``None``。

    参数含义同 :func:`image_model_capabilities`；``endpoint_kind`` 只对自定义供应商的模型有意义。
    """
    if builtin_provider is not None:
        model = catalog.get_model(builtin_provider, model_id, media_type="image")
        return None if model is None else model.max_reference_images
    if endpoint_kind == COMFYUI_KIND:
        return None
    return image_model_facts(model_id, None, entry_binding=entry_binding).max_reference_images


def image_reference_override(overrides: object | None, *, endpoint_kind: str = "python") -> int | None:
    """图片参考图上限的合法覆盖；ComfyUI 只读节点绑定，不接受覆盖。"""
    if endpoint_kind == COMFYUI_KIND:
        return None
    value = model_fact_overrides(overrides, "image").get("max_reference_images")
    return value if isinstance(value, int) else None


def image_reference_limit(
    *,
    capabilities: Iterable[ImageCapability],
    entry_limit: int | None,
    channel_limit: int | None,
    override: int | None = None,
) -> InputLimit[int]:
    """参考图上限的生效值。``capabilities`` 是生效的文生图 / 图生图（:func:`intersect_image_capabilities` 的结果）。

    图生图不在生效能力里时为不支持；否则先取条目值与通道值中较小的那个，再由 ``override`` 整维替换；两侧都为 ``None`` 且没有覆盖时为未收录。
    """
    return effective_input_limit(
        path_supported=ImageCapability.IMAGE_TO_IMAGE in frozenset(capabilities),
        declared=(entry_limit, channel_limit),
        override=override,
    )


async def backend_reference_image_limit(backend: ImageBackend, *, provider_id: str) -> InputLimit[int]:
    """一个已构造的图片后端的参考图上限生效值，供编排层裁剪与执行时的校验共用。

    通道值取后端的 ``reference_image_limit()``（可能要向供应商查询，如 OpenRouter）。自定义供应商的包装后端带着
    工厂按模型行注入的生效能力与条目上限；其余后端按 ``provider_id`` 取条目。
    """
    from lib.custom_provider.backends import CustomImageBackend

    channel_limit = await backend.reference_image_limit()
    if isinstance(backend, CustomImageBackend):
        return image_reference_limit(
            capabilities=backend.effective_capabilities,
            entry_limit=backend.entry_reference_limit,
            override=backend.reference_limit_override,
            channel_limit=channel_limit,
        )
    builtin_provider = None if is_custom_provider(provider_id) else provider_id
    return image_reference_limit(
        capabilities=effective_image_capabilities(
            backend.capabilities, model_id=backend.model, builtin_provider=builtin_provider
        ),
        entry_limit=entry_reference_limit(backend.model, builtin_provider=builtin_provider),
        override=image_reference_override(catalog.user_added_capability_overrides(builtin_provider, backend.model))
        if builtin_provider is not None
        else None,
        channel_limit=channel_limit,
    )


async def gate_image_request(backend: ImageBackend, *, provider_id: str, reference_count: int) -> None:
    """提交前的图片能力校验，生产出图与测试连接共用；不满足时抛 :class:`ImageCapabilityError`。

    ``reference_count`` 是本次请求实际带的参考图张数：为 0 时要求文生图，否则要求图生图，且参考图上限
    （:func:`backend_reference_image_limit`）接受输入、张数不超出正数上限。超限说明调用方没有按编排层同一个
    生效值裁剪，在提交前拒绝，后端不截断。
    """
    from lib.custom_provider.backends import CustomImageBackend

    if isinstance(backend, CustomImageBackend):
        # 自定义供应商：工厂已按模型行注入模型事实一侧（条目 ⊕ 能力覆盖，ComfyUI 端点不加约束）。
        available = backend.effective_capabilities
    else:
        available = effective_image_capabilities(
            backend.capabilities,
            model_id=backend.model,
            builtin_provider=None if is_custom_provider(provider_id) else provider_id,
        )
    needed = ImageCapability.IMAGE_TO_IMAGE if reference_count else ImageCapability.TEXT_TO_IMAGE
    reference_limit = await backend_reference_image_limit(backend, provider_id=provider_id) if reference_count else None
    if needed not in available or (reference_limit is not None and not reference_limit.accepts_input):
        raise ImageCapabilityError(
            "image_capability_missing_i2i" if reference_count else "image_capability_missing_t2i",
            provider=backend.name,
            model=backend.model,
        )
    bound = reference_limit.clamp_bound() if reference_limit is not None else None
    if bound is not None and reference_count > bound:
        raise ImageCapabilityError(
            "image_reference_images_exceeded", model=backend.model, limit=bound, count=reference_count
        )


def image_quality_options(entry: CatalogEntry | None, *, channel_accepts: bool) -> tuple[str, ...]:
    """有效画质可选值：调用通道接受显式画质时取条目的 ``qualities``（按数据里的顺序），否则为空。

    对应不上条目、不是图片条目或条目未收录画质取值时同样为空。
    """
    if not channel_accepts or entry is None or not isinstance(entry.facts, ImageFacts):
        return ()
    return entry.facts.qualities


def builtin_image_quality_options(provider_id: str, model_id: str) -> tuple[str, ...]:
    """内置供应商模型的有效画质可选值；供应商没有图片调用通道时抛 ``ValueError``。

    通道事实取模型挂接的图片调用端点，模型事实取 :func:`lib.catalog.query.provider_model_entry` 的条目。
    """
    accepts = channel_accepts_explicit_quality(builtin_model_spec(provider_id, "image", model_id).registry_backend)
    return image_quality_options(
        catalog.provider_model_entry(provider_id, model_id, "image") if accepts else None, channel_accepts=accepts
    )


def custom_image_quality_options(
    model_id: str, *, endpoint_spec: EndpointSpec, entry_binding: str | None = None
) -> tuple[str, ...]:
    """自定义供应商图片模型的有效画质可选值：端点的通道事实与 :func:`lib.custom_provider.model_facts.model_entry` 的条目。"""
    from lib.custom_provider.endpoints import endpoint_accepts_explicit_quality

    accepts = endpoint_accepts_explicit_quality(endpoint_spec)
    return image_quality_options(
        model_entry(model_id, "image", entry_binding) if accepts else None, channel_accepts=accepts
    )


def backend_image_quality_options(backend: ImageBackend, *, provider_id: str) -> tuple[str, ...]:
    """一个已构造的图片后端的有效画质可选值。自定义供应商的包装后端带着工厂按模型行注入的值；其余按 ``provider_id`` 取。"""
    from lib.custom_provider.backends import CustomImageBackend

    if isinstance(backend, CustomImageBackend):
        return backend.quality_options
    if is_custom_provider(provider_id):
        return ()
    try:
        return builtin_image_quality_options(provider_id, backend.model)
    except ValueError:
        # 已构造出图片后端却查不到图片调用端点：按没有画质选项处理，请求保持原有行为。
        return ()


def effective_image_quality(stored: str | None, options: tuple[str, ...]) -> str | None:
    """把项目存的画质换成生效的画质，执行与预估共用。

    有效可选值为空时忽略存值，返回 ``None``；否则原样返回存值（已去首尾空白），不对照可选值校验，
    不在可选值内时照发，由上游拒绝。``None`` 即调用通道沿用按分辨率档位推出 ``quality`` 的既有做法。
    """
    return stored if options and stored else None
