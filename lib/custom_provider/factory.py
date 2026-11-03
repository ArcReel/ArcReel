"""自定义供应商 Backend 工厂（按 endpoint 派发）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from lib.backends.image_capabilities import custom_image_model_capabilities, entry_reference_limit
from lib.backends.video_capabilities import entry_video_facts
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.capabilities import synthesize_video_capabilities_with_overrides
from lib.custom_provider.endpoints import EndpointSpec, get_endpoint_spec
from lib.custom_provider.model_facts import model_entry
from lib.custom_provider.path_overrides import effective_path_overrides

if TYPE_CHECKING:
    from lib.db.models.custom_provider import CustomProvider


def create_custom_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    endpoint: str,
    capability_overrides: object | None = None,
    path_overrides: object | None = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend:
    """按 endpoint 查 ENDPOINT_REGISTRY 并构造 Backend。

    视频后端额外注入生效能力（endpoint spec 系统判定与模型条目两侧求值 ⊕ 模型级用户覆盖），执行层据此门控，
    不直接读被包装 backend 的声明。图片后端注入模型事实一侧的文生图 / 图生图（条目 ⊕ 能力覆盖，
    ComfyUI 端点不加约束）与条目的参考图上限，执行层取它们与通道声明两侧求值的结果。

    Args:
        provider: 自定义供应商 ORM 对象（需 base_url / api_key / provider_id 属性）
        model_id: 该次调用使用的具体模型 id
        endpoint: ENDPOINT_REGISTRY 的键
        capability_overrides: 该模型的 `capability_overrides` 原始值；None = 全部跟随系统判定
        path_overrides: 该模型的 `path_overrides` 原始值；端点已不声明的请求名忽略（docs/adr/0101）
        entry_binding: 该模型的条目绑定（「模型厂商/官方模型名」）；None = 按名字对应条目

    Raises:
        ValueError: endpoint 不在 ENDPOINT_REGISTRY 中
    """
    spec = endpoint_spec or get_endpoint_spec(endpoint)
    paths = effective_path_overrides(path_overrides, spec.requests)
    backend = spec.build_backend(provider, model_id, paths)
    if isinstance(backend, CustomVideoBackend):
        merged, applied = synthesize_video_capabilities_with_overrides(
            endpoint=endpoint,
            model_id=model_id,
            overrides=capability_overrides,
            endpoint_spec=spec,
            entry_binding=entry_binding,
        )
        # 一并记住 endpoint 与路径覆盖：两者都是 backend 构造的真正输入粒度，已提交任务的续跑
        # 据此比对协议是否已被换掉（docs/adr/0054、docs/adr/0101）。通道能力与参考图槽位是否接受素材引用都按
        # 模型对应的条目认定（条目绑定优先），与读侧合成、内置供应商同一判定；请求里仍发 model_id。
        entry = model_entry(model_id, spec.media_type, entry_binding)
        # 档位查询在协议 backend 的档位感知声明上与同一份条目事实求值；ComfyUI 端点不读条目。
        entry_facts = None if spec.kind == COMFYUI_KIND else entry_video_facts(entry)
        return (
            backend.with_endpoint(endpoint, path_overrides=paths)
            .with_video_capabilities(merged, overrides=applied, entry_facts=entry_facts)
            .with_catalog_entry(entry)
        )
    if isinstance(backend, CustomImageBackend):
        return backend.with_model_facts(
            custom_image_model_capabilities(
                model_id, overrides=capability_overrides, endpoint_kind=spec.kind, entry_binding=entry_binding
            ),
            entry_reference_limit=entry_reference_limit(
                model_id, builtin_provider=None, endpoint_kind=spec.kind, entry_binding=entry_binding
            ),
        )
    return backend
