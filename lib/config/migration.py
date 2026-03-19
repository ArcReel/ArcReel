from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository
from lib.system_config import resolve_vertex_credentials_path

logger = logging.getLogger(__name__)

# JSON key → (provider, config_key, is_secret)
_PROVIDER_KEY_MAP: list[tuple[str, str, str, bool]] = [
    ("gemini_api_key", "gemini-aistudio", "api_key", True),
    ("gemini_base_url", "gemini-aistudio", "base_url", False),
    ("vertex_gcs_bucket", "gemini-vertex", "gcs_bucket", False),
    ("ark_api_key", "seedance", "api_key", True),
    ("file_service_base_url", "seedance", "file_service_base_url", False),
    ("xai_api_key", "grok", "api_key", True),
]

_GEMINI_RATE_KEYS: list[tuple[str, str]] = [
    ("gemini_image_rpm", "image_rpm"),
    ("gemini_video_rpm", "video_rpm"),
    ("gemini_request_gap", "request_gap"),
]

_SYSTEM_SETTING_KEYS: list[str] = [
    "video_generate_audio",
    "anthropic_api_key",
    "anthropic_base_url",
    "anthropic_model",
    "anthropic_default_haiku_model",
    "anthropic_default_opus_model",
    "anthropic_default_sonnet_model",
    "claude_code_subagent_model",
]

_HANDLED_KEYS = {
    "gemini_api_key", "gemini_base_url", "vertex_gcs_bucket",
    "ark_api_key", "file_service_base_url", "xai_api_key",
    "gemini_image_rpm", "gemini_video_rpm", "gemini_request_gap",
    "image_max_workers", "video_max_workers",
    "image_backend", "video_backend", "video_model", "image_model",
    "version", "updated_at",
} | set(_SYSTEM_SETTING_KEYS)


async def migrate_json_to_db(session: AsyncSession, json_path: Path) -> None:
    if not json_path.exists():
        return

    logger.info("Migrating %s to database...", json_path)
    data = json.loads(json_path.read_text())
    overrides: dict = data.get("overrides", {})

    provider_repo = ProviderConfigRepository(session)
    setting_repo = SystemSettingRepository(session)

    # 1. Provider-specific keys
    for json_key, provider, config_key, is_secret in _PROVIDER_KEY_MAP:
        value = overrides.get(json_key)
        if value is not None:
            await provider_repo.set(provider, config_key, str(value), is_secret=is_secret)

    # 1b. Vertex credentials — detect existing file
    project_root = json_path.parent.parent  # projects/.system_config.json → project root
    vertex_cred_path = resolve_vertex_credentials_path(project_root)
    if vertex_cred_path and vertex_cred_path.exists():
        await provider_repo.set("gemini-vertex", "credentials_path", str(vertex_cred_path), is_secret=False)

    # 2. Gemini rate limit keys → both aistudio and vertex
    for json_key, config_key in _GEMINI_RATE_KEYS:
        value = overrides.get(json_key)
        if value is not None:
            for p in ("gemini-aistudio", "gemini-vertex"):
                await provider_repo.set(p, config_key, str(value), is_secret=False)

    # 3. Combined backend fields
    image_backend = overrides.get("image_backend", "aistudio")
    image_model = overrides.get("image_model", "gemini-3.1-flash-image-preview")
    await setting_repo.set("default_image_backend", f"gemini-{image_backend}/{image_model}")

    video_backend = overrides.get("video_backend", "aistudio")
    video_model = overrides.get("video_model", "veo-3.1-generate-001")
    # AI Studio 使用 preview 后缀，Vertex 使用 001 后缀
    if video_backend == "aistudio":
        _model_fix = {
            "veo-3.1-generate-001": "veo-3.1-generate-preview",
            "veo-3.1-fast-generate-001": "veo-3.1-fast-generate-preview",
        }
        video_model = _model_fix.get(video_model, video_model)
    await setting_repo.set("default_video_backend", f"gemini-{video_backend}/{video_model}")

    # 4. System setting keys
    for key in _SYSTEM_SETTING_KEYS:
        value = overrides.get(key)
        if value is not None:
            await setting_repo.set(key, str(value))

    # 5. max_workers → write to all configured providers that support the media type
    configured_providers = set()
    for json_key, provider, _, _ in _PROVIDER_KEY_MAP:
        if overrides.get(json_key) is not None:
            configured_providers.add(provider)

    image_max = overrides.get("image_max_workers")
    video_max = overrides.get("video_max_workers")

    for provider_id in configured_providers:
        meta = PROVIDER_REGISTRY.get(provider_id)
        if not meta:
            continue
        if image_max is not None and "image" in meta.media_types:
            await provider_repo.set(provider_id, "image_max_workers", str(image_max), is_secret=False)
        if video_max is not None and "video" in meta.media_types:
            await provider_repo.set(provider_id, "video_max_workers", str(video_max), is_secret=False)

    # 6. Catch-all: remaining override keys → system_setting
    for key, value in overrides.items():
        if key not in _HANDLED_KEYS:
            await setting_repo.set(key, str(value))

    await session.commit()

    # 7. Rename to .bak
    bak_path = json_path.with_suffix(".json.bak")
    json_path.rename(bak_path)
    logger.info("Migration complete. Renamed to %s", bak_path)
