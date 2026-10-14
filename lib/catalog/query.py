"""目录查询：模型级信息的唯一读入口。

模型的显示名、媒体类型、能力、可选时长与分辨率、最大输出 token、价格，以及供应商在某媒体类型下的
默认模型，生产代码一律经这里读取，不直接遍历或索引注册表的 ``models``。注册表只向调用方提供
供应商一级的代码事实（凭证形态、调用通道、默认地址、并发默认）。

数据当前取自 ``lib.config.registry.PROVIDER_REGISTRY``，每次调用都按注册表的现值求值，不缓存。
返回值是目录自己的值对象 :class:`CatalogModel`，调用方不接触注册表的 ``ModelInfo``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lib.config import registry

if TYPE_CHECKING:
    from lib.billing.pricing.types import Pricing
    from lib.config.registry import ModelCapability, ModelInfo


@dataclass(frozen=True)
class CatalogModel:
    """供应商名下一个模型键在目录中的信息：模型条目的事实，加上这个供应商模型自己的事实。

    条目事实：``display_name``、``media_type``、``capabilities``、可选时长与时长约束、
    ``resolutions``、``max_output_tokens``。供应商模型事实：``default``（这个供应商在该媒体类型下的
    默认模型）、``hidden``（不出现在候选里）、``api_model_name``（发给供应商的模型名，``None`` 时用
    模型键）。价格不在这里，经 :func:`model_pricing` 取。

    字段名与顺序即设置接口里模型信息的序列化形状（``dataclasses.asdict``）。
    """

    display_name: str
    media_type: str
    capabilities: list[ModelCapability]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)
    reference_image_durations: list[int] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)
    hidden: bool = False
    api_model_name: str | None = None
    max_output_tokens: int | None = None


def _to_catalog_model(info: ModelInfo) -> CatalogModel:
    return CatalogModel(
        display_name=info.display_name,
        media_type=info.media_type,
        capabilities=info.capabilities,
        default=info.default,
        supported_durations=info.supported_durations,
        duration_resolution_constraints=info.duration_resolution_constraints,
        reference_image_durations=info.reference_image_durations,
        resolutions=info.resolutions,
        hidden=info.hidden,
        api_model_name=info.api_model_name,
        max_output_tokens=info.max_output_tokens,
    )


def _model_infos(provider_id: str) -> dict[str, ModelInfo]:
    meta = registry.PROVIDER_REGISTRY.get(provider_id)
    return meta.models if meta is not None else {}


def get_model(provider_id: str, model_id: str) -> CatalogModel | None:
    """按供应商与模型键取目录信息；供应商或模型不在目录中时返回 ``None``。"""
    info = _model_infos(provider_id).get(model_id)
    return _to_catalog_model(info) if info is not None else None


def list_models(provider_id: str) -> dict[str, CatalogModel]:
    """列出供应商的全部模型（按登记顺序，含 ``hidden`` 的模型）；供应商不在目录中时为空。"""
    return {model_id: _to_catalog_model(info) for model_id, info in _model_infos(provider_id).items()}


def default_model(provider_id: str, media_type: str) -> str | None:
    """供应商在该媒体类型下的默认模型键；没有指定时返回 ``None``。"""
    for model_id, info in _model_infos(provider_id).items():
        if info.media_type == media_type and info.default:
            return model_id
    return None


def provider_media_types(provider_id: str) -> list[str]:
    """供应商的模型覆盖到的媒体类型，升序。"""
    return sorted({info.media_type for info in _model_infos(provider_id).values()})


def provider_capabilities(provider_id: str) -> list[str]:
    """供应商全部模型的能力并集，升序。"""
    return sorted({cap for info in _model_infos(provider_id).values() for cap in info.capabilities})


def model_pricing(provider_id: str, model_id: str) -> Pricing | None:
    """模型的定价声明；模型不在目录中或没有定价时返回 ``None``。"""
    info = _model_infos(provider_id).get(model_id)
    return info.pricing if info is not None else None
