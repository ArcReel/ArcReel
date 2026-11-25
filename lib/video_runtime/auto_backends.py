"""「自动选择」的实际模型预览：跨内置与自定义默认模型层，供设置界面展示。"""

from __future__ import annotations

from lib.config.resolver import AutoResolvePurpose, ConfigResolver
from lib.custom_provider import make_provider_id
from lib.custom_provider.default_models import callable_models, filter_by_bucket
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.video_runtime.image_resolution import automatic_image_spec

_AUTO_RESOLVE_TARGETS: dict[AutoResolvePurpose, tuple[str, str | None]] = {
    "t2i": ("image", "t2i"),
    "i2i": ("image", "i2i"),
    "i2v": ("video", "i2v"),
    "r2v": ("video", "r2v"),
    "text": ("text", None),
    "audio": ("audio", None),
}


async def auto_resolved_backends(resolver: ConfigResolver) -> dict[AutoResolvePurpose, tuple[str, str] | None]:
    """各用途在项目与全局各层都留空时自动推断选中的 (provider_id, model_id)，选不出即 None。"""
    resolved: dict[AutoResolvePurpose, tuple[str, str] | None] = {}
    async with resolver.session(snapshot_settings=True) as r:
        for purpose, (media_type, generation_type) in _AUTO_RESOLVE_TARGETS.items():
            try:
                await r.require_auto_fallback(media_type)
                async with r._open_session() as (session, _):
                    if purpose in ("t2i", "i2i"):
                        snapshot = (await automatic_image_spec(session, purpose)).snapshot
                        resolved[purpose] = snapshot.provider_id, snapshot.model_id
                        continue
                    repo = CustomProviderRepository(session)
                    custom_models = await repo.list_enabled_models_by_media_type(media_type)
                    defaults = callable_models([model for model in custom_models if model.is_default], media_type)
                    candidates = await filter_by_bucket(session, defaults, generation_type)
                resolved[purpose] = (
                    (make_provider_id(candidates[0].provider_id), candidates[0].model_id) if candidates else None
                )
            except ValueError:
                resolved[purpose] = None
    return resolved
