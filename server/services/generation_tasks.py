"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver, ProviderModel

from lib.app_data_dir import app_data_dir
from lib.asset_types import ASSET_SPECS
from lib.backend_assembly import assemble_backend
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider import is_custom_provider
from lib.db.base import DEFAULT_USER_ID
from lib.gemini_shared import get_shared_rate_limiter
from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as i18n_translate
from lib.image_backends.base import ImageCapabilityError
from lib.media_generator import MediaGenerator
from lib.path_safety import safe_exists
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from lib.prompt_builders import (
    append_product_fidelity_tail,
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
)
from lib.prompt_utils import (
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    video_prompt_to_yaml,
)
from lib.providers import PROVIDER_ARK, PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_KLING, PROVIDER_OPENAI, PROVIDER_VIDU
from lib.reference_compression import ReferencePayloadFloorError
from lib.resource_paths import resource_relative_path
from lib.storyboard_sequence import (
    build_previous_storyboard_reference,
    find_storyboard_item,
    get_storyboard_items,
    group_scenes_by_segment_break,
    resolve_previous_storyboard_path,
)
from lib.thumbnail import extract_video_thumbnail
from lib.video_backends.base import VideoCapabilityError
from server.services.resolution_resolver import resolve_resolution

pm = ProjectManager(app_data_dir())
rate_limiter = get_shared_rate_limiter()
logger = logging.getLogger(__name__)

# 按 (channel, provider_name, model) 缓存 Backend 实例，避免每次任务重建 API 客户端
_backend_cache: dict[tuple[str, str, str | None], Any] = {}

# 新 provider_id → 旧 backend registry name 的映射
_PROVIDER_ID_TO_BACKEND: dict[str, str] = {
    "gemini-aistudio": PROVIDER_GEMINI,
    "gemini-vertex": PROVIDER_GEMINI,
    PROVIDER_GEMINI: PROVIDER_GEMINI,
    PROVIDER_ARK: PROVIDER_ARK,
    PROVIDER_GROK: PROVIDER_GROK,
    PROVIDER_OPENAI: PROVIDER_OPENAI,
    PROVIDER_VIDU: PROVIDER_VIDU,
}


def get_project_manager() -> ProjectManager:
    return pm


def invalidate_backend_cache() -> None:
    """清空 VideoBackend 实例缓存。在配置变更后调用。"""
    _backend_cache.clear()


async def _resolve_effective_image_backend(
    project: dict,
    payload: dict | None,
    *,
    needs_i2i: bool = False,
) -> ProviderModel:
    """图片 provider 解析的薄投影：委托 ``ConfigResolver.resolve_image_backend``。

    capability 仅在执行层确定（见 ``docs/adr/0001``）：``needs_i2i`` → i2i 槽，否则 t2i 槽。
    与 ``_resolve_video_backend`` 一致不吞解析异常——未配置供应商时让 ``ConfigResolver`` 抛出的
    清晰 ``ValueError``（"未找到可用的 image 供应商..."）直接透传，而非掩盖成空 backend 的通用错误。
    """
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory

    resolver = ConfigResolver(async_session_factory)
    capability = "i2i" if needs_i2i else "t2i"
    return await resolver.resolve_image_backend(project, payload, capability=capability)


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

    # 自定义供应商 + 简单族经统一构造缝；gemini/kling 媒体特例暂留下方命令式分支
    backend_name = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)
    if is_custom_provider(provider_name) or backend_name not in (PROVIDER_GEMINI, PROVIDER_KLING):
        backend = await assemble_backend(
            provider_id=provider_name,
            media_type="video",
            model_id=effective_model,
            resolver=resolver,
            rate_limiter=rate_limiter,
        )
        _backend_cache[cache_key] = backend
        return backend

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
    else:  # PROVIDER_KLING
        # 可灵 JWT 直连：双 secret（access_key + secret_key）而非单 api_key（见 ADR 0037）。
        db_config = await resolver.provider_config(PROVIDER_KLING)
        kwargs["auth_mode"] = "jwt"
        kwargs["access_key"] = db_config.get("access_key")
        kwargs["secret_key"] = db_config.get("secret_key")
        kwargs["model"] = effective_model
        base_url = db_config.get("base_url") or (PROVIDER_REGISTRY[PROVIDER_KLING].default_base_url)
        if base_url:
            kwargs["base_url"] = base_url

    backend = create_backend(backend_name, **kwargs)
    _backend_cache[cache_key] = backend
    return backend


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

    # 自定义供应商 + 简单族经统一构造缝；gemini/kling 媒体特例暂留下方命令式分支
    backend_name = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)
    if is_custom_provider(provider_name) or backend_name not in (PROVIDER_GEMINI, PROVIDER_KLING):
        backend = await assemble_backend(
            provider_id=provider_name,
            media_type="image",
            model_id=effective_model,
            resolver=resolver,
            rate_limiter=rate_limiter,
        )
        _backend_cache[cache_key] = backend
        return backend

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
    else:  # PROVIDER_KLING
        # 可灵 JWT 直连：双 secret（access_key + secret_key）而非单 api_key（见 ADR 0037）。
        meta = PROVIDER_REGISTRY[PROVIDER_KLING]
        db_config = await resolver.provider_config(PROVIDER_KLING)
        kwargs["auth_mode"] = "jwt"
        kwargs["access_key"] = db_config.get("access_key")
        kwargs["secret_key"] = db_config.get("secret_key")
        kwargs["model"] = effective_model
        # 两栖模型 registry 键名与 API 模型名解耦：别名键（如 kling-v3-omni-image）发真实 API 名。
        model_info = meta.models.get(effective_model) if effective_model else None
        if model_info is not None and model_info.api_model_name:
            kwargs["api_model_name"] = model_info.api_model_name
        base_url = db_config.get("base_url") or meta.default_base_url
        if base_url:
            kwargs["base_url"] = base_url

    backend = create_backend(backend_name, **kwargs)
    _backend_cache[cache_key] = backend
    return backend


async def _get_or_create_audio_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: ConfigResolver,
    *,
    default_audio_model: str | None = None,
):
    """获取或创建 AudioBackend 实例（带缓存）。"""
    effective_model = provider_settings.get("model") or default_audio_model or None
    cache_key = ("audio", provider_name, effective_model)
    if cache_key in _backend_cache:
        return _backend_cache[cache_key]

    # audio 无 gemini/kling 媒体特例：自定义 + 简单族统一经构造缝
    backend = await assemble_backend(
        provider_id=provider_name,
        media_type="audio",
        model_id=effective_model,
        resolver=resolver,
        rate_limiter=rate_limiter,
    )
    _backend_cache[cache_key] = backend
    return backend


async def _resolve_video_backend(
    project_name: str,
    resolver: ConfigResolver,
    payload: dict | None,
) -> tuple[Any | None, str, str, str]:
    """解析并构造视频后端，返回 (video_backend, video_backend_type, video_model, provider_id)。

    provider/model 的**解析**是 ``resolver.resolve_video_backend`` 的薄投影；backend **构造**
    （``_get_or_create_video_backend``）留在原地。仅在 payload 存在时创建 VideoBackend，避免
    图片任务因视频配置缺失而报错。注意：video_backend_type 仅在 video_backend 为 None
    （回退到 GeminiClient）时生效。provider_id 是 registry id（参考图压缩按它查 per-provider 上限）。
    """
    project = await asyncio.to_thread(get_project_manager().load_project, project_name) if payload else None
    resolved = await resolver.resolve_video_backend(project, payload)

    video_backend = None
    video_backend_type = "aistudio"
    mapped = _PROVIDER_ID_TO_BACKEND.get(resolved.provider_id, resolved.provider_id)
    if mapped == PROVIDER_GEMINI:
        video_backend_type = "vertex" if resolved.provider_id == "gemini-vertex" else "aistudio"

    if payload:
        provider_settings: dict = {"model": resolved.model_id} if resolved.model_id else {}
        video_backend = await _get_or_create_video_backend(
            resolved.provider_id,
            provider_settings,
            resolver,
            default_video_model=resolved.model_id or None,
        )

    return video_backend, video_backend_type, resolved.model_id, resolved.provider_id


async def get_media_generator(
    project_name: str,
    payload: dict | None = None,
    *,
    user_id: str = DEFAULT_USER_ID,
    require_image_backend: bool = True,
    needs_i2i: bool = False,
    needs_audio: bool = False,
) -> MediaGenerator:
    """创建 MediaGenerator。仅按调用场景初始化所需的 backend。

    needs_i2i: 若调用方知晓本次任务带参考图，传 True 以选 I2I 默认 backend；否则用 T2I。
    needs_audio: TTS 任务传 True，只构造 audio backend，跳过 image/video（语音任务不需要二者，
        且强行构造视频 backend 会因视频供应商缺配置而误失败）。
    """
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory

    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    resolver = ConfigResolver(async_session_factory)

    # provider_id 须在 async with 之前初始化：纯视频任务（require_image_backend=False）取不到
    # image provider，纯图任务也要拿到 video provider，两个分支各自赋值后传给 MediaGenerator。
    image_provider_id: str | None = None
    video_provider_id: str | None = None
    async with resolver.session() as r:
        image_backend = None
        video_backend = None
        audio_backend = None

        if needs_audio:
            project = await asyncio.to_thread(get_project_manager().load_project, project_name)
            resolved_audio = await r.resolve_audio_backend(project, payload)
            audio_backend = await _get_or_create_audio_backend(
                resolved_audio.provider_id,
                {},
                r,
                default_audio_model=resolved_audio.model_id or None,
            )
        else:
            if require_image_backend:
                project = await asyncio.to_thread(get_project_manager().load_project, project_name)
                resolved_image = await _resolve_effective_image_backend(project, payload, needs_i2i=needs_i2i)
                # 解析失败 → provider_id 为空，让 _get_or_create_image_backend 抛出清晰错误
                image_provider_id = resolved_image.provider_id
                image_backend = await _get_or_create_image_backend(
                    resolved_image.provider_id,
                    {},
                    r,
                    default_image_model=resolved_image.model_id or None,
                )

            # 解析 video backend（保持现有逻辑）
            video_backend, _, _, video_provider_id = await _resolve_video_backend(
                project_name,
                r,
                payload,
            )

    return MediaGenerator(
        project_path,
        rate_limiter=rate_limiter,
        image_backend=image_backend,
        video_backend=video_backend,
        audio_backend=audio_backend,
        config_resolver=resolver,
        user_id=user_id,
        image_provider_id=image_provider_id,
        video_provider_id=video_provider_id,
    )


def get_aspect_ratio(project: dict, resource_type: str) -> str:
    if resource_type == "characters":
        # 角色采用四视图横版
        return "16:9"
    if resource_type in ("scenes", "props", "products"):
        # 多视图横排版式（product sheet 同为多角度横版）
        return "16:9"
    # 优先读顶层字段；缺失时按 content_mode 推导（向后兼容）
    val = project.get("aspect_ratio")
    if isinstance(val, str):
        return val
    if isinstance(val, dict) and resource_type in val:
        return val[resource_type]
    # narration/ad 默认竖屏，drama（含未知值的历史兜底）默认横屏
    return "9:16" if project.get("content_mode", "narration") in {"narration", "ad"} else "16:9"


def _normalize_storyboard_prompt(prompt: str | dict, style: str) -> str:
    if isinstance(prompt, str):
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        return prompt

    if not isinstance(prompt, dict):
        raise ValueError("prompt must be a string or object")

    if not is_structured_image_prompt(prompt):
        raise ValueError("prompt must be a string or include scene/composition")

    scene_text = str(prompt.get("scene", "")).strip()
    if not scene_text:
        raise ValueError("prompt.scene must not be empty")

    composition_raw = prompt.get("composition")
    composition: dict = composition_raw if isinstance(composition_raw, dict) else {}
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
    """归一化视频 prompt 并在末尾追加统一文本化的反向提示词。"""
    from lib.prompt_builders import append_video_negative_tail

    if isinstance(prompt, str):
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        return append_video_negative_tail(prompt)

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
    return append_video_negative_tail(video_prompt_to_yaml(normalized_prompt))


def _get_model_default_duration(provider_name: str, model_name: str | None) -> int:
    """从 PROVIDER_REGISTRY 查找模型的 supported_durations[0]，找不到则 fallback 4。"""
    provider_meta = PROVIDER_REGISTRY.get(provider_name)
    if provider_meta and model_name:
        model_info = provider_meta.models.get(model_name)
        if model_info and model_info.supported_durations:
            return model_info.supported_durations[0]
    # 自定义供应商或 registry 中无此模型时 fallback
    return 4


def assert_duration_supported(duration: int | float | str, supported_durations: list[int]) -> None:
    """执行层能力守卫：duration 必须落在已解析 model 的 supported_durations 内。

    这是 `duration ↔ supported_durations` 唯一的权威校验家——provider 在执行时才解析
    （见 ADR-0001），故能力校验只能坐在 provider 解析之后。``supported_durations`` 为空时
    放行（能力不可解析，不更坏：保持既有行为不被本次改动弄坏）。

    duration 可能来自外部配置（payload / project.json），故安全解析字符串 / 浮点：
    可解析为整数秒（如 ``"6"`` / ``6.0``）的归一化后比较；非整数秒（如 ``4.5``）一律
    视为非法而**拒绝**，不做截断式归一化（截断会把本应拒绝的非法值静默修正）。

    校验失败抛 :class:`VideoCapabilityError`（带稳定 code），与 ImageCapabilityError 对称——
    Worker 捕获后渲染为本地化的 task.error_message。
    """
    if not supported_durations:
        return
    try:
        numeric = float(duration)
    except (TypeError, ValueError):
        raise VideoCapabilityError("video_duration_invalid", duration=duration)
    if not numeric.is_integer():
        raise VideoCapabilityError("video_duration_invalid", duration=duration)
    seconds = int(numeric)
    if seconds not in supported_durations:
        raise VideoCapabilityError(
            "video_duration_not_supported",
            duration=seconds,
            supported=", ".join(str(d) for d in supported_durations),
        )


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


def _collect_shot_product_references(project: dict, project_path: Path, item: dict) -> list[dict]:
    """产品镜头（``products_in_shot`` 非空）的产品参考集，分镜图与视频两层共用。

    每个产品：有 product sheet 时注入集为「sheet 多角度 + 原图压阵」（sheet 在前、
    原图收尾），无 sheet 时原图直注。返回 ``{"image": Path, "label": str, "name": str,
    "kind": "sheet"|"original"}`` 列表——label 供支持内联标签的后端绑定图与产品名，
    name 供高保真指令点名（指令只点名实际注入了参考的产品），kind 供截断时让 sheet
    优先存活；调用方负责把该列表排在其它参考之前（排序绝对优先）。氛围镜头
    （列表为空）返回空列表，零产品图。脏数据（products_in_shot 非列表、products
    非 dict、产品名非字符串、引用不存在的产品）按既有装配口径跳过不抛。
    """
    raw_products_in_shot = item.get("products_in_shot")
    if not isinstance(raw_products_in_shot, (list, tuple)):
        if raw_products_in_shot:
            logger.warning(
                "products_in_shot 类型异常（%s），产品参考注入跳过",
                type(raw_products_in_shot).__name__,
            )
        return []
    return collect_product_references_for_names(project, project_path, raw_products_in_shot)


def collect_product_references_for_names(
    project: dict,
    project_path: Path,
    names: Sequence[str],
) -> list[dict]:
    """按产品名列表收集产品参考集（注入二元规则的装配核心，条目语义见
    ``_collect_shot_product_references``）。分镜/视频按镜头注入与 ad 参考直出
    按 unit 注入共用此函数，保证两条路径的「sheet 在前、原图压阵」口径一致。
    """
    spec = ASSET_SPECS["product"]
    products = project.get(spec.bucket_key)
    if not isinstance(products, dict):
        products = {}
    references: list[dict] = []
    for name in names:
        if not isinstance(name, str):
            logger.warning("products_in_shot 含非字符串条目 %r，产品参考跳过", name)
            continue
        entry = products.get(name)
        if not isinstance(entry, dict):
            logger.warning("镜头引用的产品 '%s' 不在 project.json products 中，产品参考跳过", name)
            continue
        before = len(references)
        sheet = entry.get(spec.sheet_field)
        if sheet and safe_exists(project_path, sheet):
            references.append(
                {
                    "image": project_path / sheet,
                    "label": f"产品「{name}」标准多角度参考图",
                    "name": name,
                    "kind": "sheet",
                }
            )
        for original in _collect_product_reference_images(project, project_path, name) or []:
            references.append(
                {"image": original, "label": f"产品「{name}」实拍原图（保真锚点）", "name": name, "kind": "original"}
            )
        if len(references) == before:
            logger.warning("产品镜头引用的产品 '%s' 无任何可用参考图（sheet 与原图均缺失），保真注入退化为纯文本", name)
    return references


def _product_names_in_references(product_references: list[dict]) -> list[str]:
    """从产品参考集提取去重保序的产品名——高保真指令只点名实际注入了参考的产品。"""
    return list(dict.fromkeys(ref["name"] for ref in product_references))


def _product_references_for_video(generator: Any, project: dict, project_path: Path, item: dict) -> list[dict]:
    """视频层产品参考的能力门控收集：仅「首帧上可叠加参考输入」的后端注入。

    门控看 ``reference_images_with_start_frame`` 而非 ``reference_images``——后者在
    多家后端意味着与首帧互斥的「参考生视频」模式（见 ``VideoCapabilities`` docstring），
    误注入会丢弃已审核的分镜首帧甚至整请求被拒。不支持（或能力不可知）的后端返回
    空列表——正常降级、不报错，视频请求与既有图生视频路径完全一致。

    超过 ``max_reference_images`` 上限时截断，截断前把 sheet 稳定前置（跨产品 sheet
    全部排在原图之前），保证每个产品的锚定 sheet 优先存活；未触发截断时保持
    「每产品 sheet + 原图压阵」的原始顺序。end_image（首尾帧）路径与本门控无关，
    该槽位恢复使用时需复核与 max 上限的合并核算。
    """
    if not item.get("products_in_shot"):
        return []
    backend = getattr(generator, "_video_backend", None)
    caps = getattr(backend, "video_capabilities", None)
    if caps is None or not (caps.reference_images and caps.reference_images_with_start_frame):
        logger.info(
            "视频后端 %s 不支持在首帧请求上叠加参考图，产品参考二次注入跳过（正常降级）",
            getattr(backend, "name", "unknown"),
        )
        return []
    references = _collect_shot_product_references(project, project_path, item)
    max_refs = caps.max_reference_images
    if max_refs and len(references) > max_refs:
        logger.warning(
            "产品参考 %d 张超过视频后端 %s 上限 %d，sheet 前置后截断（每个产品的 sheet 优先存活）",
            len(references),
            getattr(backend, "name", "unknown"),
            max_refs,
        )
        references = sorted(references, key=lambda ref: 0 if ref["kind"] == "sheet" else 1)[:max_refs]
    return references


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


def compute_affected_fingerprints(project_name: str, task_type: str, resource_id: str) -> dict[str, int]:
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
    elif task_type == "product":
        paths.append(
            (
                f"products/{resource_id}.png",
                project_path / "products" / f"{resource_id}.png",
            )
        )
    elif task_type == "grid":
        paths.append(
            (
                f"grids/{resource_id}.png",
                project_path / "grids" / f"{resource_id}.png",
            )
        )
        # 宫格切割还会覆写多个 canonical 分镜图，实际写入的 cell 路径持久化在
        # grid 记录的 frame_chain 中，一并纳入指纹让前端对这些文件 cache-bust；
        # 记录缺失/损坏时降级为只报宫格主图。
        try:
            from lib.grid_manager import GridManager

            grid = GridManager(project_path).get(resource_id)
        except Exception:
            grid = None
        if grid is not None:
            # 记录是磁盘上的 JSON，image_path 不可直接信任：绝对路径会覆盖左操作数、
            # ../ 会越出项目目录，把任意服务器文件的存在性/mtime 暴露给前端
            project_root = project_path.resolve()
            for frame in grid.frame_chain:
                if not frame.image_path:
                    continue
                candidate = (project_path / frame.image_path).resolve()
                try:
                    # 指纹 key 用归一化后的项目相对路径：原始字符串若是项目内的
                    # 绝对路径，会把服务器路径泄漏给前端且匹配不上前端的资源 key
                    rel = candidate.relative_to(project_root).as_posix()
                except ValueError:
                    logger.warning("跳过越出项目目录的宫格 cell 路径: %s", frame.image_path)
                    continue
                paths.append((rel, candidate))
    elif task_type == "reference_video":
        paths.append(
            (
                f"reference_videos/{resource_id}.mp4",
                project_path / "reference_videos" / f"{resource_id}.mp4",
            )
        )
        paths.append(
            (
                f"reference_videos/thumbnails/{resource_id}.jpg",
                project_path / "reference_videos" / "thumbnails" / f"{resource_id}.jpg",
            )
        )
    elif task_type == "tts":
        audio_rel = resource_relative_path("audio", resource_id)
        paths.append((audio_rel, project_path / audio_rel))

    result: dict[str, int] = {}
    for rel, abs_path in paths:
        if abs_path.exists():
            result[rel] = abs_path.stat().st_mtime_ns

    return result


# (entity_type, action, label_tpl, include_script_episode)
# 三类项目级资产（character / scene / prop）的 spec 由 lib.asset_types.ASSET_SPECS 派生。
_TASK_CHANGE_SPECS: dict[str, tuple] = {
    "storyboard": ("segment", "storyboard_ready", "分镜「{}」", True),
    "video": ("segment", "video_ready", "分镜「{}」", True),
    "tts": ("segment", "tts_ready", "旁白「{}」", True),
    "grid": ("grid", "grid_ready", "宫格「{}」", True),
    "reference_video": ("reference_video_unit", "reference_video_ready", "参考视频「{}」", True),
    **{atype: (atype, "updated", f"{spec.label_zh}「{{}}」设计图", False) for atype, spec in ASSET_SPECS.items()},
}


def emit_generation_success_batch(
    *,
    task_type: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
) -> dict[str, int]:
    """发送生成/上传完成的项目变更事件，返回受影响文件的指纹（调用方可直接复用，免二次计算）。

    事件 source 由 project_change_source contextvar 决定（worker / webui 调用方各自包裹）。
    """
    spec = _TASK_CHANGE_SPECS.get(task_type)
    if spec is None:
        return {}

    entity_type, action, label_tpl, include_script_episode = spec
    asset_fingerprints = compute_affected_fingerprints(project_name, task_type, resource_id)

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
        emit_project_change_batch(project_name, [change])
    except Exception:
        logger.exception(
            "发送生成完成项目事件失败 project=%s task_type=%s resource_id=%s",
            project_name,
            task_type,
            resource_id,
        )
    return asset_fingerprints


async def execute_storyboard_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
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
        # 产品镜头：产品参考全量注入且排序绝对优先（先于角色/场景/道具 sheet），
        # 并附高保真还原指令；氛围镜头零产品图，既有装配不变。
        _product_refs = _collect_shot_product_references(_project, _project_path, _target_item)
        if _product_refs:
            _ref_images = _product_refs + (_ref_images or [])
            _prompt_text = append_product_fidelity_tail(_prompt_text, _product_names_in_references(_product_refs))
        return _project, _project_path, _prompt_text, _ref_images

    project, project_path, prompt_text, reference_images = await asyncio.to_thread(_prepare)
    _needs_i2i = bool(reference_images)

    generator = await get_media_generator(
        project_name,
        payload=payload,
        user_id=user_id,
        needs_i2i=_needs_i2i,
    )
    aspect_ratio = get_aspect_ratio(project, "storyboards")

    resolved_image = await _resolve_effective_image_backend(project, payload, needs_i2i=_needs_i2i)
    image_size = await resolve_resolution(project, resolved_image.provider_id, resolved_image.model_id)

    _, version = await generator.generate_image_async(
        prompt=prompt_text,
        resource_type="storyboards",
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
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


async def execute_tts_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """为说书模式单个 segment 合成旁白音频（同步 TTS，无续传）。

    文本来源：payload.text 显式优先；否则从脚本 segment 的 novel_text 读取。文本为空 /
    segment 找不到 / 脚本未生成一律显式 raise，绝不把空串送给 backend 合成。
    """
    script_file = payload.get("script_file")

    def _prepare() -> tuple[dict, str]:
        _project = get_project_manager().load_project(project_name)
        _text = payload.get("text") or payload.get("prompt")
        if not _text:
            if not script_file:
                raise ValueError("tts task 需要 payload.text 或 payload.script_file 之一")
            _script = get_project_manager().load_script(project_name, script_file)
            _items, _id_field, *_ = get_storyboard_items(_script)
            _resolved = find_storyboard_item(_items, _id_field, resource_id)
            if _resolved is None:
                raise ValueError(f"segment not found: {resource_id}")
            _segment, _ = _resolved
            _text = _segment.get("novel_text")
        if not isinstance(_text, str) or not _text.strip():
            raise ValueError(f"segment {resource_id} 无可合成的旁白文本（novel_text 为空）")
        return _project, _text.strip()

    project, text = await asyncio.to_thread(_prepare)

    generator = await get_media_generator(
        project_name,
        payload=payload,
        user_id=user_id,
        require_image_backend=False,
        needs_audio=True,
    )

    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory

    resolver = ConfigResolver(async_session_factory)
    voice = await resolver.resolve_narration_voice(project)
    speed = await resolver.resolve_narration_speed(project)

    _, version = await generator.generate_audio_async(
        text=text,
        resource_id=resource_id,
        voice=voice,
        speed=speed,
    )

    audio_rel = resource_relative_path("audio", resource_id)

    def _finalize():
        if script_file:
            get_project_manager().update_scene_asset(
                project_name=project_name,
                script_filename=script_file,
                scene_id=resource_id,
                asset_type="narration_audio",
                asset_path=audio_rel,
            )
        return generator.versions.get_versions("audio", resource_id)["versions"][-1]["created_at"]

    created_at = await asyncio.to_thread(_finalize)

    return {
        "version": version,
        "file_path": audio_rel,
        "created_at": created_at,
        "resource_type": "audio",
        "resource_id": resource_id,
    }


async def execute_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
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

    # 优先读取 generated_assets.storyboard_image，回退默认路径。
    # 旧宫格项目 storyboard_image 指向 scene_{id}_first.png，仍可正常解析。
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

    # 产品镜头的视频层二次注入：把产品参考注入视频请求（零额外图像成本），
    # 按后端「首帧叠加参考」能力门控——不支持的后端正常降级、不报错。
    # 首尾帧锚定不在本路径（end_image 槽位保留，capability-gated 后续增强）。
    _gated_product_refs = await asyncio.to_thread(_product_references_for_video, generator, project, project_path, item)
    product_reference_images = [ref["image"] for ref in _gated_product_refs] or None
    if product_reference_images:
        prompt_text = append_product_fidelity_tail(prompt_text, _product_names_in_references(_gated_product_refs))

    # 解析 provider / model（薄投影），供 duration fallback 和分辨率查找共用。
    # 与执行层 backend 构造同走 resolve_video_backend，确保限流/分辨率与实际调用对齐。
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory

    _resolver = ConfigResolver(async_session_factory)
    try:
        resolved_video = await _resolver.resolve_video_backend(project, payload)
        registry_provider_id = resolved_video.provider_id
        model_name = resolved_video.model_id or None
    except Exception:
        registry_provider_id, model_name = "gemini-aistudio", "veo-3.1-lite-generate-preview"

    # supported_durations 按上面已解析出的 provider/model 取（而非按 project 二次解析），
    # 确保 duration 守卫所依据的能力与实际要调用的 model 一致——历史任务 payload 携带
    # provider 覆盖时，二者不一致会用「项目默认 model 的能力」误判「payload 解析出的 model」。
    # caps 失败不得丢弃已解析出的 provider/model，否则 resolve_resolution 与默认 duration
    # 会错配。能力不可解析时留空，守卫遇空列表放行（不更坏，见 ADR-0002）。
    supported_durations: list[int] = []
    try:
        caps = await _resolver.video_capabilities_for_model(registry_provider_id, model_name or "", project)
        supported_durations = [int(d) for d in caps.get("supported_durations") or []]
    except Exception:
        supported_durations = []

    resolution = await resolve_resolution(
        project,
        registry_provider_id,
        model_name or "",
    )

    # duration 解析收口于执行层：payload > project.default_duration > caps 默认。
    # 用 ``is not None`` 而非 ``or`` 取 payload 值，避免显式 falsy 值被当作未设置。
    duration_seconds = payload.get("duration_seconds")
    if duration_seconds is None:
        duration_seconds = project.get("default_duration")
    if not duration_seconds:
        duration_seconds = (
            supported_durations[0]
            if supported_durations
            else _get_model_default_duration(registry_provider_id, model_name)
        )
    # 能力守卫：provider 解析之后的唯一权威家（见 ADR-0001）。安全解析交给守卫，
    # 此处不预先 int() 截断，避免把非整数秒静默修正成「碰巧合法」的值。
    assert_duration_supported(duration_seconds, supported_durations)

    end_image = None  # 宫格模式不再使用首尾帧，统一走普通图生视频

    _, version, _, video_uri = await generator.generate_video_async(
        prompt=prompt_text,
        resource_type="videos",
        resource_id=resource_id,
        start_image=storyboard_file,
        end_image=end_image,
        reference_images=product_reference_images,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        resolution=resolution,
        task_id=task_id,
        seed=seed,
        service_tier=service_tier,
    )

    return await _finalize_video_task(
        project_name=project_name,
        script_file=script_file,
        project_path=project_path,
        resource_id=resource_id,
        version=version,
        video_uri=video_uri,
        generator=generator,
    )


async def _finalize_video_task(
    *,
    project_name: str,
    script_file: str,
    project_path: Path,
    resource_id: str,
    version: int,
    video_uri: str | None,
    generator: Any,
) -> dict[str, Any]:
    """Normal + resume 共用的 finalize 逻辑：写 scene asset + 抽缩略图 + 返回 result dict。"""

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
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
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
    _needs_i2i = bool(reference_images)

    generator = await get_media_generator(project_name, payload=payload, user_id=user_id, needs_i2i=_needs_i2i)
    aspect_ratio = get_aspect_ratio(project, "characters")

    resolved_image = await _resolve_effective_image_backend(project, payload, needs_i2i=_needs_i2i)
    image_size = await resolve_resolution(project, resolved_image.provider_id, resolved_image.model_id)

    _, version = await generator.generate_image_async(
        prompt=full_prompt,
        resource_type="characters",
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
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


# 仅保留 design 任务的「prompt 构造器」差异；bucket_key 与 sheet 写入由 ASSET_SPECS 与
# ProjectManager._update_asset_sheet 统一派发。
_DESIGN_PROMPT_BUILDERS: dict[str, Any] = {
    "scene": build_scene_prompt,
    "prop": build_prop_prompt,
    "product": build_product_prompt,
}


def _collect_product_reference_images(project: dict, project_path: Path, resource_id: str) -> list[Path] | None:
    """产品原图（保真验收锚点）作为 sheet 标准化整理的参考输入；缺失文件跳过。"""
    entry = (project.get("products") or {}).get(resource_id) or {}
    refs = entry.get("reference_images")
    if not isinstance(refs, list):
        return None
    # safe_exists 同时兜住脏数据（非字符串）、越出项目目录的绝对路径 / `..` 穿越与文件缺失
    existing = [project_path / ref for ref in refs if safe_exists(project_path, ref)]
    if refs and not existing:
        # 声明了原图却全部缺失：下游（sheet 生成 / 镜头保真注入）静默退化会丢失保真锚定，
        # 留观测痕迹便于诊断（不阻塞——文件缺失可能是归档迁移等正常历史原因）。
        # 文案保持场景中立：本函数同时服务 sheet 生成与产品镜头参考收集两个调用方。
        logger.warning("产品 '%s' 声明了 %d 张原图但磁盘均缺失", resource_id, len(refs))
    return existing or None


# design 任务的参考图收集器差异：product 的 sheet 是「原图 → 标准多角度图」的整理，
# 原图全量注入；scene / prop 维持纯文生图。
_DESIGN_REFERENCE_COLLECTORS: dict[str, Any] = {
    "product": _collect_product_reference_images,
}


async def execute_design_task(
    kind: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """合并 execute_scene_task / execute_prop_task / execute_product_task：按 kind 查表派发。"""
    spec = ASSET_SPECS[kind]
    bucket_key = spec.bucket_key
    prompt_builder = _DESIGN_PROMPT_BUILDERS[kind]
    reference_collector = _DESIGN_REFERENCE_COLLECTORS.get(kind)

    prompt = str(payload.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError(f"prompt is required for {kind} task")

    def _prepare():
        project = get_project_manager().load_project(project_name)
        project_path = get_project_manager().get_project_path(project_name)
        if resource_id not in project.get(bucket_key, {}):
            raise ValueError(f"{kind} not found: {resource_id}")
        style = project.get("style", "")
        style_desc = project.get("style_description", "")
        full_prompt = prompt_builder(resource_id, prompt, style, style_desc)
        refs = reference_collector(project, project_path, resource_id) if reference_collector else None
        return project, full_prompt, refs

    project, full_prompt, reference_images = await asyncio.to_thread(_prepare)
    needs_i2i = bool(reference_images)

    generator = await get_media_generator(project_name, payload=payload, user_id=user_id, needs_i2i=needs_i2i)
    aspect_ratio = get_aspect_ratio(project, bucket_key)

    resolved_image = await _resolve_effective_image_backend(project, payload, needs_i2i=needs_i2i)
    image_size = await resolve_resolution(project, resolved_image.provider_id, resolved_image.model_id)

    _, version = await generator.generate_image_async(
        prompt=full_prompt,
        resource_type=bucket_key,
        resource_id=resource_id,
        reference_images=reference_images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
    )

    sheet_path = f"{bucket_key}/{resource_id}.png"

    def _finalize():
        get_project_manager()._update_asset_sheet(kind, project_name, resource_id, sheet_path)
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
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_design_task("scene", project_name, resource_id, payload, user_id=user_id)


async def execute_prop_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_design_task("prop", project_name, resource_id, payload, user_id=user_id)


async def execute_product_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_design_task("product", project_name, resource_id, payload, user_id=user_id)


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
    """Collect character/scene/prop sheet images referenced by grid scenes.

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
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
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

        _needs_i2i = bool(reference_images)
        generator = await get_media_generator(
            project_name,
            payload=payload,
            user_id=user_id,
            needs_i2i=_needs_i2i,
        )

        project = await asyncio.to_thread(get_project_manager().load_project, project_name)
        aspect_ratio = payload.get("grid_aspect_ratio") or get_aspect_ratio(project, "storyboards")

        resolved_image = await _resolve_effective_image_backend(project, payload, needs_i2i=_needs_i2i)
        # 回填 grid metadata：route 层创建/重建时无法预知 needs_i2i，由此处补齐
        grid.provider = resolved_image.provider_id
        grid.model = resolved_image.model_id
        grid_manager.save(grid)
        image_size = (
            await resolve_resolution(project, resolved_image.provider_id, resolved_image.model_id) or "2K"
        )  # 宫格图保底高分辨率

        image_path, version = await generator.generate_image_async(
            prompt=prompt_text,
            resource_type="grids",
            resource_id=resource_id,
            reference_images=reference_images,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
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
            from lib.script_editor import resolve_items

            # batch_update_scene_assets 在任一 scene_id 未命中时整批 fail-loud 回滚——避免
            # cell.save() 已写 PNG 落盘后又因 KeyError 整批回滚留下 orphan PNG,这里先 load
            # 当前剧本拿 valid id 集合,frame_chain 中已不存在的分镜(grid plan 生成后 agent
            # split/remove 改动了剧本)跳过 cell PNG 保存 + 收集到 missing 列表 + warning。
            pm = get_project_manager()
            script = pm.load_script(project_name, script_file)
            items, id_field, _kind = resolve_items(script)
            valid_ids = {str(item.get(id_field)) for item in items if isinstance(item, dict)}

            asset_updates: list[tuple[str, str, Any]] = []
            missing_ids: list[str] = []

            # 宫格已统一走普通图生视频（不再使用 first_last 模式），cell 仅作为
            # next_scene_id 的起始分镜图，文件名与普通分镜对齐为 scene_{id}.png。
            for cell, frame in zip(cells, grid.frame_chain):
                if frame.frame_type == "placeholder":
                    continue
                if frame.frame_type not in ("first", "transition"):
                    continue
                if not frame.next_scene_id:
                    continue

                if str(frame.next_scene_id) not in valid_ids:
                    missing_ids.append(str(frame.next_scene_id))
                    continue

                cell_rel = f"storyboards/scene_{frame.next_scene_id}.png"
                cell_path = storyboards_dir / f"scene_{frame.next_scene_id}.png"
                # 与 MediaGenerator 版本顺序一致：旧文件先补登再覆写、覆写后登记新版本。
                # 否则宫格重切的单元格不进版本史，版本面板的「当前版本」与磁盘内容脱节，
                # 且下一次还原/上传会让未登记的格子字节永久丢失。
                generator.versions.ensure_current_tracked("storyboards", str(frame.next_scene_id), cell_path, "")
                cell.save(cell_path, format="PNG")
                generator.versions.add_version(
                    resource_type="storyboards",
                    resource_id=str(frame.next_scene_id),
                    prompt="",
                    source_file=cell_path,
                    source="grid_split",
                    grid_id=resource_id,
                )
                frame.image_path = cell_rel
                asset_updates.append((frame.next_scene_id, "storyboard_image", cell_rel))
                asset_updates.append((frame.next_scene_id, "grid_id", resource_id))
                asset_updates.append((frame.next_scene_id, "grid_cell_index", frame.index))

            if missing_ids:
                logger.warning(
                    "grid %s: frame_chain 中以下分镜在剧本 %s 已不存在,跳过 cell 保存: %s",
                    resource_id,
                    script_file,
                    sorted(set(missing_ids)),
                )

            # Batch-write all asset updates in one script read+write pass
            if asset_updates:
                pm.batch_update_scene_assets(
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


async def _execute_reference_video_task_proxy(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Lazy proxy to avoid circular import: reference_video_tasks imports from this module."""
    from server.services.reference_video_tasks import execute_reference_video_task

    return await execute_reference_video_task(project_name, resource_id, payload, user_id=user_id, task_id=task_id)


_TASK_EXECUTORS = {
    "storyboard": execute_storyboard_task,
    "video": execute_video_task,
    "tts": execute_tts_task,
    "character": execute_character_task,
    "scene": execute_scene_task,
    "prop": execute_prop_task,
    "product": execute_product_task,
    "grid": execute_grid_task,
    "reference_video": _execute_reference_video_task_proxy,
}


async def execute_generation_task(task: dict[str, Any]) -> dict[str, Any]:
    task_type = task.get("task_type")
    project_name = task.get("project_name")
    resource_id = str(task.get("resource_id"))
    payload = task.get("payload") or {}
    user_id = task.get("user_id", DEFAULT_USER_ID)
    queue_task_id = task.get("task_id")

    if not project_name:
        raise ValueError("task.project_name is required")
    if not task_type:
        raise ValueError("task.task_type is required")

    executor = _TASK_EXECUTORS.get(task_type)
    if executor is None:
        raise ValueError(f"unsupported task_type: {task_type}")

    with project_change_source("worker"):
        try:
            result = await executor(project_name, resource_id, payload, user_id=user_id, task_id=queue_task_id)
        except (ImageCapabilityError, VideoCapabilityError, ReferencePayloadFloorError) as err:
            # Worker 后台无 request 上下文，按 DEFAULT_LOCALE 渲染稳定的 i18n 文案
            # 落到 task.error_message，前端轮询时即可看到本地化提示。
            # ReferencePayloadFloorError 对普通图/视频与 R2V 都经此渲染（R2V 走同一 dispatch catch）。
            message = i18n_translate(err.code, locale=DEFAULT_LOCALE, **err.params)
            raise RuntimeError(message) from err
        emit_generation_success_batch(
            task_type=task_type,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
        )
        return result
