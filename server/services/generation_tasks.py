"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

import asyncio
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
from lib.prompt_builders import build_character_prompt, build_prop_prompt, build_scene_prompt
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
    group_scenes_by_segment_break,
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


def _parse_project_backend(raw: str | None) -> tuple[str | None, str | None]:
    """解析 project.json 中 ``video_backend`` / ``image_backend`` 的 ``"provider/model"`` 格式。"""
    if not raw:
        return None, None
    if "/" in raw:
        provider, model = raw.split("/", 1)
        return provider, model
    return raw, None


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
        project = await asyncio.to_thread(get_project_manager().load_project, project_name)

        # 从 project.json 的 video_backend（"provider/model" 格式）解析
        provider_name, project_model = _parse_project_backend(project.get("video_backend"))

        if not provider_name:
            provider_name = default_video_provider_id
            mapped = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)
            if mapped == PROVIDER_GEMINI:
                video_backend_type = "vertex" if default_video_provider_id == "gemini-vertex" else "aistudio"

        provider_settings: dict = {"model": project_model} if project_model else {}
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

    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    resolver = ConfigResolver(async_session_factory)

    # 初始化阶段共享单一 session
    async with resolver.session() as r:
        image_backend = None
        if require_image_backend:
            image_provider_id, image_model = await r.default_image_backend()
            # payload 中的 image_provider（由入队时 _snapshot_image_backend 注入）
            if payload and payload.get("image_provider"):
                image_provider_id = payload["image_provider"]
                image_model = payload.get("image_model", "") or image_model
            else:
                # 直接从 project.json 的 image_backend（"provider/model" 格式）读取
                project = await asyncio.to_thread(get_project_manager().load_project, project_name)
                proj_provider, proj_model = _parse_project_backend(project.get("image_backend"))
                if proj_provider:
                    # 仅当 provider 相同时才复用全局默认 model，避免跨 provider model 不匹配
                    image_model = proj_model or (image_model if proj_provider == image_provider_id else None)
                    image_provider_id = proj_provider
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
    if resource_type in ("clues", "scenes", "props"):
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


def _collect_sheet_paths(
    project: dict,
    project_path: Path,
    items: list[dict],
    *,
    char_field: str,
    scene_field: str,
    prop_field: str,
    max_count: int = 0,
) -> tuple[list[Path], set[str]]:
    """Collect character_sheet, scene_sheet and prop_sheet paths from scene/segment items.

    Returns (list of existing Paths, set of relative sheet strings for dedup).
    If *max_count* > 0 collection stops after that many images.
    """
    seen: set[str] = set()
    paths: list[Path] = []

    characters = project.get("characters", {})
    project_scenes = project.get("scenes", {})
    project_props = project.get("props", {})

    for item in items:
        for char_name in item.get(char_field, []):
            sheet = characters.get(char_name, {}).get("character_sheet")
            if sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    paths.append(path)
                    seen.add(sheet)
        for scene_name in item.get(scene_field, []):
            sheet = project_scenes.get(scene_name, {}).get("scene_sheet")
            if sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    paths.append(path)
                    seen.add(sheet)
        for prop_name in item.get(prop_field, []):
            sheet = project_props.get(prop_name, {}).get("prop_sheet")
            if sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    paths.append(path)
                    seen.add(sheet)
        if max_count and len(paths) >= max_count:
            break

    return paths, seen


def _collect_reference_images(
    project: dict,
    project_path: Path,
    target_item: dict,
    *,
    char_field: str,
    scene_field: str,
    prop_field: str,
    extra_reference_images: list[str] | None = None,
    previous_storyboard_path: Path | None = None,
) -> list[object] | None:
    sheet_paths, _ = _collect_sheet_paths(
        project, project_path, [target_item], char_field=char_field, scene_field=scene_field, prop_field=prop_field
    )
    reference_images: list[object] = list(sheet_paths)

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
                f"props/{resource_id}.png",
                project_path / "props" / f"{resource_id}.png",
            )
        )
    elif task_type == "scene":
        paths.append(
            (
                f"scenes/{resource_id}.png",
                project_path / "scenes" / f"{resource_id}.png",
            )
        )
    elif task_type == "prop":
        paths.append(
            (
                f"props/{resource_id}.png",
                project_path / "props" / f"{resource_id}.png",
            )
        )
    elif task_type == "grid":
        paths.append(
            (
                f"grids/{resource_id}.png",
                project_path / "grids" / f"{resource_id}.png",
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
    "scene": ("scene", "updated", "场景「{}」设计图", False),
    "prop": ("prop", "updated", "道具「{}」设计图", False),
    "clue": ("prop", "updated", "道具「{}」设计图", False),  # 兼容别名
    "grid": ("grid", "grid_ready", "宫格「{}」", True),
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

    def _prepare():
        _project = get_project_manager().load_project(project_name)
        _project_path = get_project_manager().get_project_path(project_name)
        _script = get_project_manager().load_script(project_name, script_file)
        _items, _id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(_script)

        _resolved = find_storyboard_item(_items, _id_field, resource_id)
        if _resolved is None:
            raise ValueError(f"scene/segment not found: {resource_id}")
        _target_item, _ = _resolved

        _prev_path = resolve_previous_storyboard_path(_project_path, _items, _id_field, resource_id)
        _prompt_text = _normalize_storyboard_prompt(prompt, _project.get("style", ""))
        _ref_images = _collect_reference_images(
            _project,
            _project_path,
            _target_item,
            char_field=_char_field,
            scene_field=_scene_field,
            prop_field=_prop_field,
            extra_reference_images=payload.get("extra_reference_images") or [],
            previous_storyboard_path=_prev_path,
        )
        return _project, _project_path, _prompt_text, _ref_images

    project, project_path, prompt_text, reference_images = await asyncio.to_thread(_prepare)

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

    def _finalize():
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="storyboard_image",
            asset_path=f"storyboards/scene_{resource_id}.png",
        )
        return generator.versions.get_versions("storyboards", resource_id)["versions"][-1]["created_at"]

    created_at = await asyncio.to_thread(_finalize)

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

    def _load():
        _pm = get_project_manager()
        _project = _pm.load_project(project_name)
        _project_path = _pm.get_project_path(project_name)
        _script = _pm.load_script(project_name, script_file)
        _items, _id_field, _, _, _ = get_storyboard_items(_script)
        _resolved = find_storyboard_item(_items, _id_field, resource_id)
        _item = _resolved[0] if _resolved else {}
        return _project, _project_path, _item

    project, project_path, item = await asyncio.to_thread(_load)
    generator = await get_media_generator(project_name, payload=payload, user_id=user_id)

    # 优先从 generated_assets.storyboard_image 读取（宫格模式写 _first.png），回退到默认路径
    assets = item.get("generated_assets", {})
    storyboard_rel = assets.get("storyboard_image") if isinstance(assets, dict) else None
    if storyboard_rel:
        storyboard_file = project_path / storyboard_rel
    else:
        storyboard_file = project_path / "storyboards" / f"scene_{resource_id}.png"
    if not storyboard_file.exists():
        raise ValueError(f"storyboard not found: {storyboard_file.name}")

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

    end_image = None  # 宫格模式不再使用首尾帧，统一走普通图生视频

    _, version, _, video_uri = await generator.generate_video_async(
        prompt=prompt_text,
        resource_type="videos",
        resource_id=resource_id,
        start_image=storyboard_file,
        end_image=end_image,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        resolution=resolution,
        seed=seed,
        service_tier=service_tier,
    )

    def _update_video_metadata():
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

    await asyncio.to_thread(_update_video_metadata)

    # 提取视频首帧作为缩略图
    video_file = project_path / f"videos/scene_{resource_id}.mp4"
    thumbnail_file = project_path / f"thumbnails/scene_{resource_id}.jpg"
    if await extract_video_thumbnail(video_file, thumbnail_file):
        await asyncio.to_thread(
            get_project_manager().update_scene_asset,
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_thumbnail",
            asset_path=f"thumbnails/scene_{resource_id}.jpg",
        )
    else:
        thumbnail_file.unlink(missing_ok=True)

    created_at = await asyncio.to_thread(
        lambda: generator.versions.get_versions("videos", resource_id)["versions"][-1]["created_at"]
    )

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

    def _prepare_char():
        _project = get_project_manager().load_project(project_name)
        _project_path = get_project_manager().get_project_path(project_name)
        if resource_id not in _project.get("characters", {}):
            raise ValueError(f"character not found: {resource_id}")
        _char_data = _project["characters"][resource_id]
        _style = _project.get("style", "")
        _style_desc = _project.get("style_description", "")
        _full_prompt = build_character_prompt(resource_id, prompt, _style, _style_desc)
        _ref_images = None
        _ref_path = _char_data.get("reference_image")
        if _ref_path:
            _full_ref = _project_path / _ref_path
            if _full_ref.exists():
                _ref_images = [_full_ref]
        return _project, _full_prompt, _ref_images

    project, full_prompt, reference_images = await asyncio.to_thread(_prepare_char)

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

    def _finalize_char():
        def _set_character_sheet(p: dict) -> None:
            p["characters"][resource_id]["character_sheet"] = sheet_path

        get_project_manager().update_project(project_name, _set_character_sheet)
        return generator.versions.get_versions("characters", resource_id)["versions"][-1]["created_at"]

    created_at = await asyncio.to_thread(_finalize_char)

    return {
        "version": version,
        "file_path": f"characters/{resource_id}.png",
        "created_at": created_at,
        "resource_type": "characters",
        "resource_id": resource_id,
    }


_DESIGN_TASK_SPECS: dict[str, dict[str, Any]] = {
    "scene": {
        "bucket_key": "scenes",
        "prompt_builder": build_scene_prompt,
        "update_sheet": lambda pm, proj, rid, path: pm.update_scene_sheet(proj, rid, path),
    },
    "prop": {
        "bucket_key": "props",
        "prompt_builder": build_prop_prompt,
        "update_sheet": lambda pm, proj, rid, path: pm.update_prop_sheet(proj, rid, path),
    },
}


async def execute_design_task(
    kind: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """合并 execute_scene_task / execute_prop_task：按 kind 查表派发。"""
    spec = _DESIGN_TASK_SPECS[kind]
    bucket_key = spec["bucket_key"]
    prompt_builder = spec["prompt_builder"]
    update_sheet = spec["update_sheet"]

    prompt = str(payload.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError(f"prompt is required for {kind} task")

    def _prepare():
        project = get_project_manager().load_project(project_name)
        if resource_id not in project.get(bucket_key, {}):
            raise ValueError(f"{kind} not found: {resource_id}")
        style = project.get("style", "")
        style_desc = project.get("style_description", "")
        full_prompt = prompt_builder(resource_id, prompt, style, style_desc)
        return project, full_prompt

    project, full_prompt = await asyncio.to_thread(_prepare)

    generator = await get_media_generator(project_name, payload=payload, user_id=user_id)
    aspect_ratio = get_aspect_ratio(project, bucket_key)

    _, version = await generator.generate_image_async(
        prompt=full_prompt,
        resource_type=bucket_key,
        resource_id=resource_id,
        aspect_ratio=aspect_ratio,
        image_size="1K",
    )

    sheet_path = f"{bucket_key}/{resource_id}.png"

    def _finalize():
        update_sheet(get_project_manager(), project_name, resource_id, sheet_path)
        return generator.versions.get_versions(bucket_key, resource_id)["versions"][-1]["created_at"]

    created_at = await asyncio.to_thread(_finalize)

    return {
        "version": version,
        "file_path": sheet_path,
        "created_at": created_at,
        "resource_type": bucket_key,
        "resource_id": resource_id,
    }


async def execute_scene_task(
    project_name: str, resource_id: str, payload: dict[str, Any], *, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    return await execute_design_task("scene", project_name, resource_id, payload, user_id=user_id)


async def execute_prop_task(
    project_name: str, resource_id: str, payload: dict[str, Any], *, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    return await execute_design_task("prop", project_name, resource_id, payload, user_id=user_id)


def _group_scenes_by_segment_break(items: list[dict], id_field: str) -> list[list[dict]]:
    """Groups consecutive scene dicts, breaking at segment_break=True.

    Delegates to :func:`lib.storyboard_sequence.group_scenes_by_segment_break`.
    """
    return group_scenes_by_segment_break(items, id_field)


def _collect_grid_reference_images(
    project_path: Path,
    payload: dict[str, Any],
    scene_ids: list[str],
) -> tuple[list[object] | None, list[dict]]:
    """Collect character/clue sheet images referenced by grid scenes.

    Returns a tuple of ``(image_paths, metadata)``:
    - *image_paths*: up to 6 :class:`~pathlib.Path` objects for the generation API.
    - *metadata*: list of dicts ``{path, name, ref_type}`` for persisting in
      :class:`~lib.grid.models.GridGeneration`.
    """
    project_json = project_path / "project.json"
    if not project_json.exists():
        return None, []

    import json

    project = json.loads(project_json.read_text(encoding="utf-8"))

    script_file = payload.get("script_file")
    if not script_file:
        return None, []

    script_path = project_path / "scripts" / script_file
    if not script_path.exists():
        return None, []

    script = json.loads(script_path.read_text(encoding="utf-8"))

    items, id_field, char_field, scene_field, prop_field = get_storyboard_items(script)

    scene_id_set = set(scene_ids)
    matched_items = [item for item in items if str(item.get(id_field, "")) in scene_id_set]

    characters = project.get("characters", {})
    project_scenes = project.get("scenes", {})
    project_props = project.get("props", {})

    seen: set[str] = set()
    paths: list[Path] = []
    metadata: list[dict] = []
    max_count = 6

    for item in matched_items:
        for char_name in item.get(char_field, []):
            sheet = characters.get(char_name, {}).get("character_sheet")
            if sheet and sheet not in seen:
                p = project_path / sheet
                if p.exists():
                    paths.append(p)
                    seen.add(sheet)
                    metadata.append({"path": sheet, "name": char_name, "ref_type": "character"})
        for scene_name in item.get(scene_field, []):
            sheet = project_scenes.get(scene_name, {}).get("scene_sheet")
            if sheet and sheet not in seen:
                p = project_path / sheet
                if p.exists():
                    paths.append(p)
                    seen.add(sheet)
                    metadata.append({"path": sheet, "name": scene_name, "ref_type": "scene"})
        for prop_name in item.get(prop_field, []):
            sheet = project_props.get(prop_name, {}).get("prop_sheet")
            if sheet and sheet not in seen:
                p = project_path / sheet
                if p.exists():
                    paths.append(p)
                    seen.add(sheet)
                    metadata.append({"path": sheet, "name": prop_name, "ref_type": "prop"})
        if len(paths) >= max_count:
            break

    return list(paths[:max_count]) or None, metadata[:max_count]


async def execute_grid_task(
    project_name: str, resource_id: str, payload: dict[str, Any], *, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    """Execute a grid image generation task.

    resource_id is the grid_id. Steps:
    1. Load GridGeneration, set status to generating
    2. Generate image via MediaGenerator
    3. Split grid image into cells
    4. Assign cell images to scenes in the script
    5. Mark completed
    """
    from PIL import Image

    from lib.grid.splitter import split_grid_image
    from lib.grid_manager import GridManager

    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    grid_manager = GridManager(project_path)

    # a) Load grid
    grid = grid_manager.get(resource_id)
    if grid is None:
        raise ValueError(f"grid not found: {resource_id}")

    script_file = grid.script_file

    try:
        # b) Set status to generating
        grid.status = "generating"
        grid.error_message = None
        grid_manager.save(grid)

        # c) Build reference images + metadata
        from lib.grid.models import ReferenceImage

        reference_images, ref_metadata = await asyncio.to_thread(
            _collect_grid_reference_images, project_path, payload, grid.scene_ids
        )
        grid.reference_images = [ReferenceImage.from_dict(m) for m in ref_metadata] if ref_metadata else []
        grid_manager.save(grid)

        # d) Generate grid image
        prompt_text = payload.get("prompt") or grid.prompt
        if not prompt_text:
            raise ValueError("prompt is required for grid task")

        generator = await get_media_generator(
            project_name,
            payload=payload,
            user_id=user_id,
        )

        project = await asyncio.to_thread(get_project_manager().load_project, project_name)
        aspect_ratio = payload.get("grid_aspect_ratio") or get_aspect_ratio(project, "storyboards")

        image_path, version = await generator.generate_image_async(
            prompt=prompt_text,
            resource_type="grids",
            resource_id=resource_id,
            reference_images=reference_images,
            aspect_ratio=aspect_ratio,
            image_size="2K",
        )

        # e) Set grid_image_path, status to splitting
        grid.grid_image_path = f"grids/{resource_id}.png"
        grid.status = "splitting"
        grid_manager.save(grid)

        # f) Split the grid image
        grid_image = Image.open(image_path)
        video_aspect_ratio = get_aspect_ratio(project, "videos")
        cells = split_grid_image(grid_image, grid.rows, grid.cols, video_aspect_ratio)

        # g) Assign cells to scenes
        storyboards_dir = project_path / "storyboards"
        storyboards_dir.mkdir(parents=True, exist_ok=True)

        def _assign_cells():
            import shutil

            asset_updates: list[tuple[str, str, str]] = []

            for cell, frame in zip(cells, grid.frame_chain):
                if frame.frame_type == "placeholder":
                    continue

                if frame.frame_type == "first" and frame.next_scene_id:
                    # Save as first frame (storyboard_image) for next_scene_id
                    cell_path = storyboards_dir / f"scene_{frame.next_scene_id}_first.png"
                    cell.save(cell_path, format="PNG")
                    frame.image_path = f"storyboards/scene_{frame.next_scene_id}_first.png"
                    asset_updates.append((frame.next_scene_id, "storyboard_image", frame.image_path))
                    # Write grid provenance
                    asset_updates.append((frame.next_scene_id, "grid_id", resource_id))
                    asset_updates.append((frame.next_scene_id, "grid_cell_index", frame.index))

                elif frame.frame_type == "transition":
                    # Save as last frame of prev scene
                    if frame.prev_scene_id:
                        last_path = storyboards_dir / f"scene_{frame.prev_scene_id}_last.png"
                        cell.save(last_path, format="PNG")
                        last_rel = f"storyboards/scene_{frame.prev_scene_id}_last.png"
                        asset_updates.append((frame.prev_scene_id, "storyboard_last_image", last_rel))
                    # Copy as first frame of next scene (same image, avoid re-encoding)
                    if frame.next_scene_id:
                        first_path = storyboards_dir / f"scene_{frame.next_scene_id}_first.png"
                        if frame.prev_scene_id:
                            shutil.copy2(last_path, first_path)
                        else:
                            cell.save(first_path, format="PNG")
                        frame.image_path = f"storyboards/scene_{frame.next_scene_id}_first.png"
                        asset_updates.append((frame.next_scene_id, "storyboard_image", frame.image_path))
                        # Write grid provenance
                        asset_updates.append((frame.next_scene_id, "grid_id", resource_id))
                        asset_updates.append((frame.next_scene_id, "grid_cell_index", frame.index))

            # Batch-write all asset updates in one script read+write pass
            if asset_updates:
                get_project_manager().batch_update_scene_assets(
                    project_name=project_name,
                    script_filename=script_file,
                    updates=asset_updates,
                )

        await asyncio.to_thread(_assign_cells)

        # h) Set status to completed
        grid.status = "completed"
        grid_manager.save(grid)

    except Exception:
        grid.status = "failed"
        import traceback

        grid.error_message = traceback.format_exc()
        grid_manager.save(grid)
        raise

    created_at = grid.created_at

    return {
        "version": version,
        "file_path": f"grids/{resource_id}.png",
        "created_at": created_at,
        "resource_type": "grids",
        "resource_id": resource_id,
    }


_TASK_EXECUTORS = {
    "storyboard": execute_storyboard_task,
    "video": execute_video_task,
    "character": execute_character_task,
    "scene": execute_scene_task,
    "prop": execute_prop_task,
    "clue": execute_prop_task,  # 兼容别名
    "grid": execute_grid_task,
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
