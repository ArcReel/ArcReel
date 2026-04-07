"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver

from lib import PROJECT_ROOT
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider import is_custom_provider
from lib.db.base import DEFAULT_USER_ID
from lib.gemini_shared import get_shared_rate_limiter
from lib.media_generator import MediaGenerator
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from lib.prompt_builders import build_character_prompt, build_clue_prompt
from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    video_prompt_to_yaml,
)
from lib.providers import PROVIDER_ARK, PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_OPENAI
from lib.storyboard_sequence import (
    build_previous_storyboard_reference,
    find_storyboard_item,
    get_storyboard_items,
    resolve_previous_storyboard_path,
)
from lib.thumbnail import extract_video_thumbnail

pm = ProjectManager(PROJECT_ROOT / "projects")
rate_limiter = get_shared_rate_limiter()
logger = logging.getLogger(__name__)

# 按 (channel, provider_name, model) 缓存 Backend 实例，避免每次任务重建 API 客户端
_backend_cache: dict[tuple[str, ...], Any] = {}

# 各供应商默认视频分辨率
DEFAULT_VIDEO_RESOLUTION: dict[str, str] = {
    PROVIDER_GEMINI: "1080p",
    PROVIDER_ARK: "720p",
    PROVIDER_GROK: "720p",
    PROVIDER_OPENAI: "720p",
}

# 新 provider_id → 旧 backend registry name 的映射
_PROVIDER_ID_TO_BACKEND: dict[str, str] = {
    "gemini-aistudio": PROVIDER_GEMINI,
    "gemini-vertex": PROVIDER_GEMINI,
    PROVIDER_GEMINI: PROVIDER_GEMINI,
    PROVIDER_ARK: PROVIDER_ARK,
    PROVIDER_GROK: PROVIDER_GROK,
    PROVIDER_OPENAI: PROVIDER_OPENAI,
}


def get_project_manager() -> ProjectManager:
    return pm


def invalidate_backend_cache() -> None:
    """清空 VideoBackend 实例缓存。在配置变更后调用。"""
    _backend_cache.clear()


async def _create_custom_backend(provider_name: str, model_id: str | None, media_type: str):
    """自定义供应商的 backend 创建路径。"""
    from lib.custom_provider import parse_provider_id
    from lib.custom_provider.factory import create_custom_backend
    from lib.db import async_session_factory
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    async with async_session_factory() as session:
        repo = CustomProviderRepository(session)
        db_id = parse_provider_id(provider_name)
        provider = await repo.get_provider(db_id)
        if provider is None:
            raise ValueError(f"自定义供应商 {provider_name} 不存在")
        if model_id:
            # 校验 model_id 仍存在且已启用，否则回退到默认模型
            from sqlalchemy import select

            from lib.db.models.custom_provider import CustomProviderModel

            stmt = select(CustomProviderModel).where(
                CustomProviderModel.provider_id == db_id,
                CustomProviderModel.model_id == model_id,
                CustomProviderModel.media_type == media_type,
                CustomProviderModel.is_enabled == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            if result.scalar_one_or_none() is None:
                logger.warning("自定义模型 %s/%s 已不存在或已禁用，回退到默认模型", provider_name, model_id)
                model_id = None

        if not model_id:
            default_model = await repo.get_default_model(db_id, media_type)
            if default_model:
                model_id = default_model.model_id
            else:
                raise ValueError(f"自定义供应商 {provider_name} 没有默认 {media_type} 模型")
        return create_custom_backend(provider=provider, model_id=model_id, media_type=media_type)


async def _get_or_create_video_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_video_model: str | None = None,
):
    """获取或创建 VideoBackend 实例（带缓存）。

    provider_name 可以是旧格式（gemini/seedance/grok）或新格式（gemini-aistudio/gemini-vertex）。
    通过 resolver 按需加载供应商配置。
    default_video_model: 全局默认视频模型，当 provider_settings 中无 model 时作为 fallback。
    """
    from lib.video_backends import create_backend

    effective_model = provider_settings.get("model") or default_video_model or None
    cache_key = ("video", provider_name, effective_model)
    if cache_key in _backend_cache:
        return _backend_cache[cache_key]

    # 自定义供应商走独立工厂路径
    if is_custom_provider(provider_name):
        backend = await _create_custom_backend(provider_name, effective_model, "video")
        _backend_cache[cache_key] = backend
        return backend

    # 解析 provider_id → backend registry name
    backend_name = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)

    kwargs: dict = {}
    if backend_name == PROVIDER_GEMINI:
        # 确定 backend_type（aistudio 或 vertex）
        if provider_name == "gemini-vertex":
            kwargs["backend_type"] = "vertex"
        elif provider_name == "gemini-aistudio":
            kwargs["backend_type"] = "aistudio"
        else:
            kwargs["backend_type"] = "aistudio"

        config_provider_id = "gemini-vertex" if kwargs["backend_type"] == "vertex" else "gemini-aistudio"
        db_config = await resolver.provider_config(config_provider_id)
        kwargs["api_key"] = db_config.get("api_key")
        kwargs["rate_limiter"] = rate_limiter
        kwargs["video_model"] = effective_model
    else:
        await _fill_simple_provider_kwargs(backend_name, resolver, kwargs, effective_model)

    backend = create_backend(backend_name, **kwargs)
    _backend_cache[cache_key] = backend
    return backend


async def _fill_simple_provider_kwargs(
    backend_name: str,
    resolver: ConfigResolver,
    kwargs: dict,
    effective_model: str | None,
) -> None:
    """Ark/Grok/OpenAI 等简单供应商的通用配置填充。"""
    db_config = await resolver.provider_config(backend_name)
    kwargs["api_key"] = db_config.get("api_key")
    kwargs["model"] = effective_model
    if base_url := db_config.get("base_url"):
        kwargs["base_url"] = base_url


async def _get_or_create_image_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_image_model: str | None = None,
):
    """获取或创建 ImageBackend 实例（带缓存）。"""
    from lib.image_backends import create_backend

    effective_model = provider_settings.get("model") or default_image_model or None
    cache_key = ("image", provider_name, effective_model)
    if cache_key in _backend_cache:
        return _backend_cache[cache_key]

    # 自定义供应商走独立工厂路径
    if is_custom_provider(provider_name):
        backend = await _create_custom_backend(provider_name, effective_model, "image")
        _backend_cache[cache_key] = backend
        return backend

    backend_name = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)

    kwargs: dict = {}
    if backend_name == PROVIDER_GEMINI:
        if provider_name == "gemini-vertex":
            kwargs["backend_type"] = "vertex"
        else:
            kwargs["backend_type"] = "aistudio"
        config_id = "gemini-vertex" if kwargs["backend_type"] == "vertex" else "gemini-aistudio"
        db_config = await resolver.provider_config(config_id)
        kwargs["api_key"] = db_config.get("api_key")
        kwargs["base_url"] = db_config.get("base_url")
        kwargs["rate_limiter"] = rate_limiter
        kwargs["image_model"] = effective_model
    else:
        await _fill_simple_provider_kwargs(backend_name, resolver, kwargs, effective_model)

    backend = create_backend(backend_name, **kwargs)
    _backend_cache[cache_key] = backend
    return backend


async def _resolve_video_backend(
    project_name: str,
    resolver: ConfigResolver,
    payload: dict | None,
) -> tuple[Any | None, str, str]:
    """解析视频后端，返回 (video_backend, video_backend_type, video_model)。

    仅在 payload 存在时创建 VideoBackend，避免图片任务因视频配置缺失而报错。
    注意：video_backend_type 仅在 video_backend 为 None（回退到 GeminiClient）时生效，
    因此只需要在全局默认回退分支中设置。
    """
    default_video_provider_id, video_model = await resolver.default_video_backend()
    video_backend = None
    video_backend_type = "aistudio"

    if payload:
        # provider 统一从项目配置 → 全局默认解析，调用方无需传递
        project = get_project_manager().load_project(project_name)
        provider_name = project.get("video_provider")
        if not provider_name:
            provider_name = default_video_provider_id
            mapped = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)
            if mapped == PROVIDER_GEMINI:
                video_backend_type = "vertex" if default_video_provider_id == "gemini-vertex" else "aistudio"
        provider_settings = project.get("video_provider_settings", {}).get(provider_name, {})
        video_backend = await _get_or_create_video_backend(
            provider_name,
            provider_settings,
            resolver,
            default_video_model=video_model,
        )

    return video_backend, video_backend_type, video_model


async def get_media_generator(
    project_name: str,
    payload: dict | None = None,
    *,
    user_id: str = DEFAULT_USER_ID,
    require_image_backend: bool = True,
) -> MediaGenerator:
    """创建 MediaGenerator。仅按调用场景初始化所需的 backend。"""
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory

    project_path = get_project_manager().get_project_path(project_name)
    resolver = ConfigResolver(async_session_factory)

    # 初始化阶段共享单一 session
    async with resolver.session() as r:
        image_backend = None
        if require_image_backend:
            image_provider_id, image_model = await r.default_image_backend()
            if payload and payload.get("image_provider"):
                image_provider_id = payload["image_provider"]
                image_model = payload.get("image_model", "") or image_model
            image_backend = await _get_or_create_image_backend(
                image_provider_id,
                {},
                r,
                default_image_model=image_model,
            )

        # 解析 video backend（保持现有逻辑）
        video_backend, _, _ = await _resolve_video_backend(
            project_name,
            r,
            payload,
        )

    # 传原始 resolver 给 MediaGenerator（后续调用在 session scope 外）
    return MediaGenerator(
        project_path,
        rate_limiter=rate_limiter,
        image_backend=image_backend,
        video_backend=video_backend,
        config_resolver=resolver,
        user_id=user_id,
    )


def get_aspect_ratio(project: dict, resource_type: str) -> str:
    if resource_type == "characters":
        return "3:4"
    if resource_type == "clues":
        return "16:9"
    # 优先读顶层字段；缺失时按 content_mode 推导（向后兼容）
    val = project.get("aspect_ratio")
    if isinstance(val, str):
        return val
    if isinstance(val, dict) and resource_type in val:
        return val[resource_type]
    return "9:16" if project.get("content_mode", "narration") == "narration" else "16:9"


def _normalize_storyboard_prompt(prompt: str | dict, style: str) -> str:
    if isinstance(prompt, str):
        return prompt

    if not isinstance(prompt, dict):
        raise ValueError("prompt must be a string or object")

    if not is_structured_image_prompt(prompt):
        raise ValueError("prompt must be a string or include scene/composition")

    scene_text = str(prompt.get("scene", "")).strip()
    if not scene_text:
        raise ValueError("prompt.scene must not be empty")

    composition = prompt.get("composition") if isinstance(prompt.get("composition"), dict) else {}
    normalized_prompt = {
        "scene": scene_text,
        "composition": {
            "shot_type": str(composition.get("shot_type") or "Medium Shot"),
            "lighting": str(composition.get("lighting", "") or ""),
            "ambiance": str(composition.get("ambiance", "") or ""),
        },
    }
    return image_prompt_to_yaml(normalized_prompt, style)


def _normalize_video_prompt(prompt: str | dict) -> str:
    if isinstance(prompt, str):
        return prompt

    if not isinstance(prompt, dict):
        raise ValueError("prompt must be a string or object")

    if not is_structured_video_prompt(prompt):
        raise ValueError("prompt must be a string or include action/camera_motion")

    action_text = str(prompt.get("action", "")).strip()
    if not action_text:
        raise ValueError("prompt.action must not be empty")

    dialogue = prompt.get("dialogue", [])
    if dialogue is None:
        dialogue = []
    if not isinstance(dialogue, list):
        raise ValueError("prompt.dialogue must be an array")

    normalized_dialogue = []
    for item in dialogue:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker", "") or "").strip()
        line = str(item.get("line", "") or "").strip()
        if speaker or line:
            normalized_dialogue.append({"speaker": speaker, "line": line})

    normalized_prompt: dict[str, Any] = {
        "action": action_text,
        "camera_motion": str(prompt.get("camera_motion", "") or "") or "Static",
        "ambiance_audio": str(prompt.get("ambiance_audio", "") or ""),
        "dialogue": normalized_dialogue,
    }
    return video_prompt_to_yaml(normalized_prompt)


def _get_model_default_duration(provider_name: str, model_name: str | None) -> int:
    """从 PROVIDER_REGISTRY 查找模型的 supported_durations[0]，找不到则 fallback 4。"""
    provider_meta = PROVIDER_REGISTRY.get(provider_name)
    if provider_meta and model_name:
        model_info = provider_meta.models.get(model_name)
        if model_info and model_info.supported_durations:
            return model_info.supported_durations[0]
    # 自定义供应商或 registry 中无此模型时 fallback
    return 4


def _collect_reference_images(
    project: dict,
    project_path: Path,
    target_item: dict,
    *,
    char_field: str,
    clue_field: str,
    extra_reference_images: list[str] | None = None,
    previous_storyboard_path: Path | None = None,
) -> list[object] | None:
    reference_images: list[object] = []

    for char_name in target_item.get(char_field, []):
        char_data = project.get("characters", {}).get(char_name, {})
        sheet = char_data.get("character_sheet")
        if sheet:
            path = project_path / sheet
            if path.exists():
                reference_images.append(path)

    for clue_name in target_item.get(clue_field, []):
        clue_data = project.get("clues", {}).get(clue_name, {})
        sheet = clue_data.get("clue_sheet")
        if sheet:
            path = project_path / sheet
            if path.exists():
                reference_images.append(path)

    for extra in extra_reference_images or []:
        extra_path = Path(extra)
        if not extra_path.is_absolute():
            extra_path = project_path / extra_path
        if extra_path.exists():
            reference_images.append(extra_path)

    if previous_storyboard_path and previous_storyboard_path.exists():
        reference_images.append(build_previous_storyboard_reference(previous_storyboard_path))

    return reference_images or None


def _resolve_script_episode(project_name: str, script_file: str | None) -> int | None:
    if not script_file:
        return None
    try:
        script = get_project_manager().load_script(project_name, script_file)
    except Exception:
        return None

    episode = script.get("episode")
    if isinstance(episode, int):
        return episode
    return None


def _compute_affected_fingerprints(project_name: str, task_type: str, resource_id: str) -> dict[str, int]:
    """计算受影响文件的 mtime 指纹"""
    try:
        project_path = get_project_manager().get_project_path(project_name)
    except Exception:
        return {}

    paths: list[tuple[str, Path]] = []

    if task_type == "storyboard":
        paths.append(
            (
                f"storyboards/scene_{resource_id}.png",
                project_path / "storyboards" / f"scene_{resource_id}.png",
            )
        )
    elif task_type == "video":
        paths.append(
            (
                f"videos/scene_{resource_id}.mp4",
                project_path / "videos" / f"scene_{resource_id}.mp4",
            )
        )
        paths.append(
            (
                f"thumbnails/scene_{resource_id}.jpg",
                project_path / "thumbnails" / f"scene_{resource_id}.jpg",
            )
        )
    elif task_type == "character":
        paths.append(
            (
                f"characters/{resource_id}.png",
                project_path / "characters" / f"{resource_id}.png",
            )
        )
    elif task_type == "clue":
        paths.append(
            (
                f"clues/{resource_id}.png",
                project_path / "clues" / f"{resource_id}.png",
            )
        )

    result: dict[str, int] = {}
    for rel, abs_path in paths:
        if abs_path.exists():
            result[rel] = abs_path.stat().st_mtime_ns

    return result


# (entity_type, action, label_tpl, include_script_episode)
_TASK_CHANGE_SPECS: dict[str, tuple] = {
    "storyboard": ("segment", "storyboard_ready", "分镜「{}」", True),
    "video": ("segment", "video_ready", "分镜「{}」", True),
    "character": ("character", "updated", "角色「{}」设计图", False),
    "clue": ("clue", "updated", "线索「{}」设计图", False),
}


def _emit_generation_success_batch(
    *,
    task_type: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
) -> None:
    spec = _TASK_CHANGE_SPECS.get(task_type)
    if spec is None:
        return

    entity_type, action, label_tpl, include_script_episode = spec
    asset_fingerprints = _compute_affected_fingerprints(project_name, task_type, resource_id)

    change: dict[str, Any] = {
        "entity_type": entity_type,
        "action": action,
        "entity_id": resource_id,
        "label": label_tpl.format(resource_id),
        "focus": None,
        "important": True,
        "asset_fingerprints": asset_fingerprints,
    }
    if include_script_episode:
        script_file = str(payload.get("script_file") or "") or None
        change["script_file"] = script_file
        change["episode"] = _resolve_script_episode(project_name, script_file)

    try:
        emit_project_change_batch(project_name, [change], source="worker")
    except Exception:
        logger.exception(
            "发送生成完成项目事件失败 project=%s task_type=%s resource_id=%s",
            project_name,
            task_type,
            resource_id,
        )


async def execute_storyboard_task(
    project_name: str, resource_id: str, payload: dict[str, Any], *, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for storyboard task")

    prompt = payload.get("prompt")
    if prompt is None:
        raise ValueError("prompt is required for storyboard task")

    project = get_project_manager().load_project(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    script = get_project_manager().load_script(project_name, script_file)
    items, id_field, char_field, clue_field = get_storyboard_items(script)

    resolved = find_storyboard_item(items, id_field, resource_id)
    if resolved is None:
        raise ValueError(f"scene/segment not found: {resource_id}")
    target_item, _ = resolved

    previous_storyboard_path = resolve_previous_storyboard_path(
        project_path,
        items,
        id_field,
        resource_id,
    )

    prompt_text = _normalize_storyboard_prompt(prompt, project.get("style", ""))
    reference_images = _collect_reference_images(
        project,
        project_path,
        target_item,
        char_field=char_field,
        clue_field=clue_field,
        extra_reference_images=payload.get("extra_reference_images") or [],
        previous_storyboard_path=previous_storyboard_path,
    )

    generator = await get_media_generator(
        project_name,
        payload=payload,
        user_id=user_id,
    )
    aspect_ratio = get_aspect_ratio(project, "storyboards")

    _, version = await generator.generate_image_async(
        prompt=prompt_text,
        resource_type="storyboards",
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size="1K",
    )

    get_project_manager().update_scene_asset(
        project_name=project_name,
        script_filename=script_file,
        scene_id=resource_id,
        asset_type="storyboard_image",
        asset_path=f"storyboards/scene_{resource_id}.png",
    )

    created_at = generator.versions.get_versions("storyboards", resource_id)["versions"][-1]["created_at"]

    return {
        "version": version,
        "file_path": f"storyboards/scene_{resource_id}.png",
        "created_at": created_at,
        "resource_type": "storyboards",
        "resource_id": resource_id,
    }


async def execute_video_task(
    project_name: str, resource_id: str, payload: dict[str, Any], *, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for video task")

    prompt = payload.get("prompt")
    if prompt is None:
        raise ValueError("prompt is required for video task")

    project = get_project_manager().load_project(project_name)
    project_path = get_project_manager().get_project_path(project_name)
    generator = await get_media_generator(project_name, payload=payload, user_id=user_id)

    storyboard_file = project_path / "storyboards" / f"scene_{resource_id}.png"
    if not storyboard_file.exists():
        raise ValueError(f"storyboard not found: scene_{resource_id}.png")

    prompt_text = _normalize_video_prompt(prompt)
    aspect_ratio = get_aspect_ratio(project, "videos")
    seed = payload.get("seed")
    service_tier = payload.get("video_provider_settings", {}).get("service_tier", "default")

    # 解析 provider / model，供 duration fallback 和分辨率查找共用
    provider_settings = payload.get("video_provider_settings", {})
    model_name = provider_settings.get("model")
    # payload 中 video_provider 由任务入队时设置；project 中存的是 video_backend（"provider/model" 格式）
    provider_name = payload.get("video_provider")
    registry_provider_id = provider_name  # 用于 PROVIDER_REGISTRY 查找的原始 provider_id
    if not provider_name:
        video_backend = project.get("video_backend") or ""
        if "/" in video_backend:
            provider_name, model_name = video_backend.split("/", 1)
            registry_provider_id = provider_name
    if not provider_name:
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        _resolver = ConfigResolver(async_session_factory)
        try:
            default_provider_id, default_model_id = await _resolver.default_video_backend()
        except Exception:
            default_provider_id, default_model_id = "gemini-aistudio", "veo-3.1-lite-generate-preview"
        registry_provider_id = default_provider_id
        model_name = model_name or default_model_id
        provider_name = _PROVIDER_ID_TO_BACKEND.get(default_provider_id, default_provider_id)
    # 将新 provider_id 映射为旧名称以查找分辨率
    resolution_key = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)
    video_model_settings = project.get("video_model_settings", {})
    model_settings = video_model_settings.get(model_name, {}) if model_name else {}
    resolution = model_settings.get("resolution") or DEFAULT_VIDEO_RESOLUTION.get(resolution_key, "1080p")

    # duration fallback: payload > project.default_duration > supported_durations[0] > 4
    duration_seconds = payload.get("duration_seconds") or project.get("default_duration")
    if not duration_seconds:
        duration_seconds = _get_model_default_duration(registry_provider_id, model_name)

    _, version, _, video_uri = await generator.generate_video_async(
        prompt=prompt_text,
        resource_type="videos",
        resource_id=resource_id,
        start_image=storyboard_file,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        resolution=resolution,
        seed=seed,
        service_tier=service_tier,
    )

    get_project_manager().update_scene_asset(
        project_name=project_name,
        script_filename=script_file,
        scene_id=resource_id,
        asset_type="video_clip",
        asset_path=f"videos/scene_{resource_id}.mp4",
    )

    if video_uri:
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_uri",
            asset_path=video_uri,
        )

    # 提取视频首帧作为缩略图
    video_file = project_path / f"videos/scene_{resource_id}.mp4"
    thumbnail_file = project_path / f"thumbnails/scene_{resource_id}.jpg"
    if await extract_video_thumbnail(video_file, thumbnail_file):
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_thumbnail",
            asset_path=f"thumbnails/scene_{resource_id}.jpg",
        )
    else:
        # 提取失败时清除旧缩略图文件，避免展示与新视频不匹配的封面
        thumbnail_file.unlink(missing_ok=True)

    created_at = generator.versions.get_versions("videos", resource_id)["versions"][-1]["created_at"]

    return {
        "version": version,
        "file_path": f"videos/scene_{resource_id}.mp4",
        "created_at": created_at,
        "resource_type": "videos",
        "resource_id": resource_id,
        "video_uri": video_uri,
    }


async def execute_character_task(
    project_name: str, resource_id: str, payload: dict[str, Any], *, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError("prompt is required for character task")

    project = get_project_manager().load_project(project_name)
    project_path = get_project_manager().get_project_path(project_name)

    if resource_id not in project.get("characters", {}):
        raise ValueError(f"character not found: {resource_id}")

    char_data = project["characters"][resource_id]
    style = project.get("style", "")
    style_description = project.get("style_description", "")
    full_prompt = build_character_prompt(resource_id, prompt, style, style_description)

    reference_images = None
    ref_path = char_data.get("reference_image")
    if ref_path:
        full_ref = project_path / ref_path
        if full_ref.exists():
            reference_images = [full_ref]

    generator = await get_media_generator(project_name, payload=payload, user_id=user_id)
    aspect_ratio = get_aspect_ratio(project, "characters")

    _, version = await generator.generate_image_async(
        prompt=full_prompt,
        resource_type="characters",
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size="1K",
    )

    sheet_path = f"characters/{resource_id}.png"

    def _set_character_sheet(p: dict) -> None:
        p["characters"][resource_id]["character_sheet"] = sheet_path

    get_project_manager().update_project(project_name, _set_character_sheet)

    created_at = generator.versions.get_versions("characters", resource_id)["versions"][-1]["created_at"]

    return {
        "version": version,
        "file_path": f"characters/{resource_id}.png",
        "created_at": created_at,
        "resource_type": "characters",
        "resource_id": resource_id,
    }


async def execute_clue_task(
    project_name: str, resource_id: str, payload: dict[str, Any], *, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError("prompt is required for clue task")

    project = get_project_manager().load_project(project_name)

    if resource_id not in project.get("clues", {}):
        raise ValueError(f"clue not found: {resource_id}")

    clue_data = project["clues"][resource_id]
    style = project.get("style", "")
    style_description = project.get("style_description", "")
    clue_type = clue_data.get("type", "prop")
    full_prompt = build_clue_prompt(resource_id, prompt, clue_type, style, style_description)

    generator = await get_media_generator(project_name, payload=payload, user_id=user_id)
    aspect_ratio = get_aspect_ratio(project, "clues")

    _, version = await generator.generate_image_async(
        prompt=full_prompt,
        resource_type="clues",
        resource_id=resource_id,
        aspect_ratio=aspect_ratio,
        image_size="1K",
    )

    sheet_path = f"clues/{resource_id}.png"

    def _set_clue_sheet(p: dict) -> None:
        p["clues"][resource_id]["clue_sheet"] = sheet_path

    get_project_manager().update_project(project_name, _set_clue_sheet)

    created_at = generator.versions.get_versions("clues", resource_id)["versions"][-1]["created_at"]

    return {
        "version": version,
        "file_path": f"clues/{resource_id}.png",
        "created_at": created_at,
        "resource_type": "clues",
        "resource_id": resource_id,
    }


_TASK_EXECUTORS = {
    "storyboard": execute_storyboard_task,
    "video": execute_video_task,
    "character": execute_character_task,
    "clue": execute_clue_task,
}


async def execute_generation_task(task: dict[str, Any]) -> dict[str, Any]:
    task_type = task.get("task_type")
    project_name = task.get("project_name")
    resource_id = str(task.get("resource_id"))
    payload = task.get("payload") or {}
    user_id = task.get("user_id", DEFAULT_USER_ID)

    if not project_name:
        raise ValueError("task.project_name is required")

    executor = _TASK_EXECUTORS.get(task_type)
    if executor is None:
        raise ValueError(f"unsupported task_type: {task_type}")

    with project_change_source("worker"):
        result = await executor(project_name, resource_id, payload, user_id=user_id)
        _emit_generation_success_batch(
            task_type=task_type,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
        )
        return result
