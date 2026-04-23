"""按 project > legacy > custom_default > None 解析每次生成调用的分辨率。

设计：见 docs/superpowers/specs/2026-04-23-resolution-param-refactor-design.md §2
"""

from __future__ import annotations


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
