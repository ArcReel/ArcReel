"""按 project > legacy > custom_default > None 解析每次生成调用的分辨率。

设计：见 docs/superpowers/specs/2026-04-23-resolution-param-refactor-design.md §2
"""

from __future__ import annotations


async def get_custom_resolution_default(
    provider_name: str | None,
    model_id: str | None,
) -> str | None:
    """若是自定义供应商，返回该模型的默认 resolution（CustomProviderModel.resolution）。"""
    from lib.custom_provider import is_custom_provider

    if not provider_name or not model_id or not is_custom_provider(provider_name):
        return None
    from lib.custom_provider import parse_provider_id
    from lib.db import async_session_factory
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    try:
        db_id = parse_provider_id(provider_name)
    except ValueError:
        # 兜底：provider_name 虽以 "custom-" 开头但后缀不是整数（测试 mock 或脏数据）
        return None

    async with async_session_factory() as session:
        repo = CustomProviderRepository(session)
        model = await repo.get_model_by_ids(db_id, model_id)
        if model is None:
            return None
        return model.resolution


def resolve_resolution(
    project: dict,
    provider_id: str,
    model_id: str,
    *,
    custom_default: str | None = None,
) -> str | None:
    """按以下顺序返回第一个非空值，否则 None（表示调用时不传）：

    1. project.model_settings["<provider_id>/<model_id>"].resolution
    2. project.video_model_settings[model_id].resolution  (legacy read)
    3. custom_default (仅自定义供应商传入)
    4. None
    """
    key = f"{provider_id}/{model_id}"
    model_settings = project.get("model_settings") or {}
    entry = model_settings.get(key) or {}
    override = entry.get("resolution")
    if override:
        return override

    legacy_root = project.get("video_model_settings") or {}
    legacy_entry = legacy_root.get(model_id) or {}
    legacy = legacy_entry.get("resolution")
    if legacy:
        return legacy

    if custom_default:
        return custom_default

    return None
