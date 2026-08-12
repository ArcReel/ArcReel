"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.asset_types import (
    ASSET_SPECS,
    normalize_asset_bucket,
    normalize_asset_name,
    resolve_asset_key,
    validate_asset_name,
)
from lib.audio_utils import (
    AUDIO_REFERENCE_MAX_BYTES,
    AUDIO_REFERENCE_MAX_SECONDS,
    AUDIO_REFERENCE_MIN_SECONDS,
    probe_audio_duration_seconds,
    probe_existing_audio_duration_seconds,
)
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import constrain_durations, video_bucket_for_generation_mode
from lib.db.base import DEFAULT_USER_ID
from lib.generation_queue import CompensableGenerationResult
from lib.narration_delivery import (
    USE_TTS,
    NarratedVideoDurationBlockedError,
    NarrationDeliveryRequestOptions,
    TtsSynthesisSettings,
    build_narration_audio_basis,
    canonical_narration_text,
    prepare_current_narrated_video_duration,
    prepare_narrated_video_duration,
    register_narration_audio_transactionally,
)
from lib.path_safety import safe_exists, try_safe_join
from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import (
    EpisodeScriptReboundError,
    ProjectManager,
    find_episode,
    get_project_manager,
    is_reference_video_project,
)
from lib.prompt_builders import (
    append_product_fidelity_tail,
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
)
from lib.prompt_utils import (
    build_drama_video_prompt,
    build_drama_video_prompt_from_legacy_dialogue,
    image_prompt_to_yaml,
    is_structured_image_prompt,
    is_structured_video_prompt,
    strip_voice_profiles,
    video_prompt_to_yaml,
)
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.script_models import resolve_content_mode
from lib.script_skeleton import SKELETON_ENTITY_TYPES, SKELETON_ITEM_NOUNS, resolve_script_kind
from lib.speech_composition import SpeechAdmissionError, admit_script_unit
from lib.storyboard_sequence import (
    build_previous_storyboard_reference,
    find_storyboard_item,
    get_storyboard_items,
    group_scenes_by_segment_break,
    resolve_previous_storyboard_path,
)
from lib.thumbnail import extract_video_thumbnail
from lib.video_backends.base import VideoCapabilityError
from lib.video_visual_provenance import build_storyboard_video_visual_basis, resolve_video_aspect_ratio
from server.services.generation_context import (
    AudioLaneRequest,
    ImageLaneRequest,
    VideoLaneRequest,
    resolve_generation_context,
)
from server.services.narration_delivery_tasks import (
    ResolvedTtsSettingsResolver,
    current_selected_video_tier,
    require_generated_video_covers_current_tts,
    resolve_storyboard_video_inputs,
    reuse_current_video_for_tier,
    tts_task_in_progress,
)

logger = logging.getLogger(__name__)


def get_aspect_ratio(project: dict, resource_type: str) -> str:
    if resource_type == "characters":
        # 角色采用四视图横版
        return "16:9"
    if resource_type in ("scenes", "props", "products"):
        # 多视图横排版式（product sheet 同为多角度横版）
        return "16:9"
    return resolve_video_aspect_ratio(project, resource_type)


def _normalize_storyboard_prompt(prompt: str | dict, style: str) -> str:
    """归一化分镜图 prompt 并在末尾追加统一文本化的反向提示词。"""
    from lib.prompt_builders import append_image_negative_tail

    if isinstance(prompt, str):
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        return append_image_negative_tail(prompt)

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
    return append_image_negative_tail(image_prompt_to_yaml(normalized_prompt, style))


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
        "voice_profiles": prompt.get("voice_profiles") or [],
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
    放行（能力不可解析，不更坏：不拒绝一个校验层判断不了的 duration）。

    duration 可能来自外部配置（payload / project.json），故安全解析字符串 / 浮点：
    可解析为整数秒（如 ``"6"`` / ``6.0``）的归一化后比较；非整数秒（如 ``4.5``）一律
    视为非法而**拒绝**，不做截断式归一化（截断会把本应拒绝的非法值静默修正）。

    校验失败抛 :class:`VideoCapabilityError`（带稳定 code），与 ImageCapabilityError 对称——
    Worker 按 code + params 落 task.error_message，文案由读侧 Translator 渲染。
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


def _collect_sheet_references(
    project: dict,
    project_path: Path,
    items: list[dict],
    *,
    char_field: str | None,
    scene_field: str,
    prop_field: str,
    max_count: int = 0,
) -> tuple[list[dict], set[str]]:
    """Collect character_sheet, scene_sheet and prop_sheet references from scene/segment items.

    Returns (list of ``{"image": Path, "label": 资产名}`` dicts, set of relative
    sheet strings for dedup). If *max_count* > 0 collection stops after that many images.

    label 取剧本条目里的资产名，与 prompt 里的专名严格一致——供支持内联标签的
    后端（如 Gemini）把参考图与 prompt 专名显式绑定，不再依赖文件名推断。

    剧本里的资产名与资产桶 key 可能是 NFC/NFD 中的任一形态（登记闸口落 NFC，存量剧本
    与桶均无需迁移），索引前按 ``lib.asset_types`` 的比对坐标系归一，label 保留剧本原文。

    ``char_field`` 为 ``None`` 表示该骨架无逐条角色名单字段（video_units：角色以
    references 条目形态存在），``item.get(None) or []`` 天然跳过角色 sheet 收集。
    """
    seen: set[str] = set()
    refs: list[dict] = []

    characters = normalize_asset_bucket(project.get("characters"))
    project_scenes = normalize_asset_bucket(project.get("scenes"))
    project_props = normalize_asset_bucket(project.get("props"))

    for item in items:
        for char_name in item.get(char_field) or []:
            if not isinstance(char_name, str):
                continue
            char_data = characters.get(normalize_asset_name(char_name))
            sheet = char_data.get("character_sheet") if isinstance(char_data, dict) else None
            if isinstance(sheet, str) and sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    refs.append({"image": path, "label": char_name})
                    seen.add(sheet)
        for scene_name in item.get(scene_field) or []:
            if not isinstance(scene_name, str):
                continue
            scene_data = project_scenes.get(normalize_asset_name(scene_name))
            sheet = scene_data.get("scene_sheet") if isinstance(scene_data, dict) else None
            if isinstance(sheet, str) and sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    refs.append({"image": path, "label": scene_name})
                    seen.add(sheet)
        for prop_name in item.get(prop_field) or []:
            if not isinstance(prop_name, str):
                continue
            prop_data = project_props.get(normalize_asset_name(prop_name))
            sheet = prop_data.get("prop_sheet") if isinstance(prop_data, dict) else None
            if isinstance(sheet, str) and sheet and sheet not in seen:
                path = project_path / sheet
                if path.exists():
                    refs.append({"image": path, "label": prop_name})
                    seen.add(sheet)
        if max_count and len(refs) >= max_count:
            break

    return (refs[:max_count] if max_count else refs), seen


def _collect_reference_images(
    project: dict,
    project_path: Path,
    target_item: dict,
    *,
    char_field: str | None,
    scene_field: str,
    prop_field: str,
    extra_reference_images: list[str] | None = None,
    previous_storyboard_path: Path | None = None,
) -> list[object] | None:
    sheet_refs, _ = _collect_sheet_references(
        project, project_path, [target_item], char_field=char_field, scene_field=scene_field, prop_field=prop_field
    )
    reference_images: list[object] = list(sheet_refs)

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
    """产品镜头（``products_in_shot`` 非空）的产品参考集，用于分镜图生成。

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
    ``_collect_shot_product_references``）。分镜图按镜头注入与 ad 参考直出
    按 unit 注入共用此函数，保证两条路径的「sheet 在前、原图压阵」口径一致。
    """
    spec = ASSET_SPECS["product"]
    products = normalize_asset_bucket(project.get(spec.bucket_key))
    references: list[dict] = []
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            logger.warning("products_in_shot 含非字符串条目 %r，产品参考跳过", name)
            continue
        canonical = normalize_asset_name(name)
        if canonical in seen:
            continue
        seen.add(canonical)
        entry = products.get(canonical)
        if not isinstance(entry, dict):
            logger.warning("镜头引用的产品 '%s' 不在 project.json products 中，产品参考跳过", name)
            continue
        before = len(references)
        sheet = entry.get(spec.sheet_field)
        if sheet and safe_exists(project_path, sheet):
            references.append(
                {
                    "image": project_path / sheet,
                    "label": f"产品「{canonical}」标准多角度参考图",
                    "name": canonical,
                    "kind": "sheet",
                }
            )
        for original in _collect_product_reference_images(project, project_path, canonical) or []:
            references.append(
                {
                    "image": original,
                    "label": f"产品「{canonical}」实拍原图（保真锚点）",
                    "name": canonical,
                    "kind": "original",
                }
            )
        if len(references) == before:
            logger.warning("产品镜头引用的产品 '%s' 无任何可用参考图（sheet 与原图均缺失），保真注入退化为纯文本", name)
    return references


def _product_names_in_references(product_references: list[dict]) -> list[str]:
    """从产品参考集提取去重保序的产品名——高保真指令只点名实际注入了参考的产品。"""
    return list(dict.fromkeys(ref["name"] for ref in product_references))


def _episode_from_script(script: dict[str, Any] | None) -> int | None:
    if not isinstance(script, dict):
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
    elif task_type in ("grid", "grid_split"):
        paths.append(
            (
                f"grids/{resource_id}.png",
                project_path / "grids" / f"{resource_id}.png",
            )
        )
        # 宫格切分会覆写多个 canonical 分镜图，实际写入的 cell 路径持久化在
        # grid 记录的 frame_chain 中，一并纳入指纹让前端对这些文件 cache-bust；
        # 记录缺失/损坏时降级为只报宫格主图。生成事件（"grid"）也带上 cell 指纹：
        # 生成本身不再触碰分镜格，未变更文件的 mtime 指纹与前端已持有值相同，无副作用。
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
                candidate = try_safe_join(project_root, frame.image_path)
                if candidate is None:
                    logger.warning("跳过越出项目目录的宫格 cell 路径: %s", frame.image_path)
                    continue
                # 指纹 key 用归一化后的项目相对路径：原始字符串若是项目内的
                # 绝对路径，会把服务器路径泄漏给前端且匹配不上前端的资源 key
                rel = candidate.relative_to(project_root).as_posix()
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
# storyboard / video / reference_video 不在此表——三者按剧本骨架种类（segments/scenes/shots/
# video_units）动态派生 entity_type 与条目名词，见 _SKELETON_DRIVEN_TASK_ACTIONS，避免恒发
# ``segment``/「分镜」而与分镜级事件（project_events.py）名词不一致。
_TASK_CHANGE_SPECS: dict[str, tuple] = {
    "grid": ("grid", "grid_ready", "宫格「{}」", True),
    "grid_split": ("grid", "grid_split_done", "宫格「{}」切分", True),
    "voice_sample": ("character", "voice_sample_ready", "「{}」试听样本", False),
    **{atype: (atype, "updated", f"{spec.label_zh}「{{}}」设计图", False) for atype, spec in ASSET_SPECS.items()},
}

# 骨架驱动的任务类型 → 完成事件 action。entity_type/条目名词按项目剧本当前骨架种类
# （resolve_script_kind，与分镜级事件同一判定）动态解析，不按 task_type 恒定硬编码。
_SKELETON_DRIVEN_TASK_ACTIONS: dict[str, str] = {
    "storyboard": "storyboard_ready",
    "video": "video_ready",
    "reference_video": "reference_video_ready",
    "tts": "tts_ready",
}

# reference_video 的条目标签沿用「参考视频」措辞（区别于分镜级事件的骨架名词「视频单元」，
# 两者服务不同场景：此为任务完成通知的条目文案，不随骨架名词收敛）；storyboard/video 未列出，
# 回退到骨架名词本身（分镜/场景/镜头），与同项目分镜级事件同口径。
_SKELETON_TASK_LABEL_NOUNS: dict[str, str] = {
    "reference_video": "参考视频",
    "tts": "旁白",
}


def _load_event_script(project_name: str, script_file: str | None) -> dict[str, Any] | None:
    """加载完成事件所属剧本一次，供骨架种类与 episode 共用；缺失/损坏时返回 None。

    调用方对 None 各自兜底（骨架种类回退 ``"segments"``、episode 回退 ``None``），
    不让剧本加载失败导致通知发送中断。
    """
    if not script_file:
        return None
    try:
        return get_project_manager().load_script(project_name, script_file)
    except Exception:
        return None


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
    if task_type == "image_edit":
        # 编辑完成事件与「同一资源的生成完成事件」同形状：按 payload.resource_type 派发到
        # 既有 spec 表（storyboard 走骨架驱动、四类资产走 ASSET_SPECS 派生表），entity/action/
        # 指纹与生成路径一致，前端既有的 SSE fingerprint 刷新零改动即可覆盖编辑完成。
        task_type = str(payload.get("resource_type") or "")

    script_file = str(payload.get("script_file") or "") or None
    # 单次加载剧本，骨架种类与 episode 共用，避免同一 script_file 双解析。
    script = _load_event_script(project_name, script_file)

    action = _SKELETON_DRIVEN_TASK_ACTIONS.get(task_type)
    if action is not None:
        if task_type == "reference_video":
            # reference_video 任务的资源身份恒为 video unit；即使剧本加载失败，完成事件也
            # 仍须使用 reference_unit 锚点，故不依赖剧本形状取证。
            kind = "video_units"
        else:
            kind = resolve_script_kind(script) if isinstance(script, dict) else "segments"
        entity_type = SKELETON_ENTITY_TYPES.get(kind, "segment")
        noun = _SKELETON_TASK_LABEL_NOUNS.get(task_type) or SKELETON_ITEM_NOUNS.get(kind, "分镜")
        label_tpl = f"{noun}「{{}}」"
        include_script_episode = True
    else:
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
        change["script_file"] = script_file
        change["episode"] = _episode_from_script(script)

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

    ctx = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        user_id=user_id,
        image=ImageLaneRequest(capability="i2i" if _needs_i2i else "t2i"),
    )
    generator = ctx.generator
    aspect_ratio = get_aspect_ratio(project, "storyboards")
    image_size = ctx.image.resolution

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


def _resolve_tts_task_items(
    script: dict[str, Any],
    *,
    reference_video_route: bool,
) -> tuple[list[dict[str, Any]], str, str]:
    """Resolve TTS units from the generation route fixed at task start."""

    if not reference_video_route:
        return resolve_items(script)
    return resolve_items({"video_units": script.get("video_units", [])})


async def execute_tts_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """为一个 narrator-owned script unit 合成独立旁白音频。"""
    script_file = payload.get("script_file")

    def _prepare() -> tuple[dict, Path, str, Any | None, int | None, bool]:
        pm = get_project_manager()
        current_project = pm.load_project(project_name)
        reference_video_route = is_reference_video_project(current_project)
        project_path = pm.get_project_path(project_name)
        if script_file:
            script = pm.load_script(project_name, script_file)
            items, id_field, kind = _resolve_tts_task_items(
                script,
                reference_video_route=reference_video_route,
            )
            item = next(
                (
                    candidate
                    for candidate in items
                    if isinstance(candidate, dict) and str(candidate.get(id_field)) == str(resource_id)
                ),
                None,
            )
            if item is None:
                raise ValueError(f"segment not found: {resource_id}")
            admission = admit_script_unit(kind, item)
            text = canonical_narration_text(admission.preparation)
            if not admission.allowed:
                if not text:
                    raise ValueError(f"segment {resource_id} 无可合成的旁白文本")
                raise SpeechAdmissionError(admission)
            if not text:
                raise ValueError(f"segment {resource_id} 无可合成的旁白文本")
            episode = ProjectManager.resolve_episode_from_script(script, str(script_file))
            return current_project, project_path, text, admission.preparation, episode, reference_video_route

        legacy_text = payload.get("text") or payload.get("prompt")
        if not isinstance(legacy_text, str) or not legacy_text.strip():
            raise ValueError("tts task 需要 payload.text 或 payload.script_file 之一")
        return current_project, project_path, legacy_text.strip(), None, None, reference_video_route

    project, project_path, text, preparation, episode, reference_video_route = await asyncio.to_thread(_prepare)

    ctx = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        user_id=user_id,
        audio=AudioLaneRequest(),
    )
    generator = ctx.generator
    voice = ctx.audio.narration_voice
    speed = ctx.audio.narration_speed
    settings = TtsSynthesisSettings(
        provider_id=ctx.audio.provider_model.provider_id,
        model_id=ctx.audio.backend_model,
        voice=voice,
        speed=speed,
    )
    basis = build_narration_audio_basis(preparation, settings) if preparation is not None else None

    audio_rel = resource_relative_path("audio", resource_id)
    duration_seconds: float | None = None
    missing_narration_audio = object()
    prior_narration_audio: object = missing_narration_audio
    prior_manifest_entry: ArtifactManifestEntry | None = None
    prior_manifest_captured = False

    async def _measure_staged(staged_path: Path) -> None:
        nonlocal duration_seconds
        duration_seconds = await probe_existing_audio_duration_seconds(staged_path)
        if duration_seconds is None or not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise RuntimeError("generated narration audio duration is unavailable")

    def _commit_staged(staged_path: Path, output_path: Path) -> int:
        nonlocal prior_narration_audio
        if script_file is None or preparation is None or episode is None or basis is None:
            return generator.versions.commit_staged_version(
                resource_type="audio",
                resource_id=resource_id,
                prompt=text,
                staged_file=staged_path,
                current_file=output_path,
                tts_provider_id=settings.provider_id,
                tts_model_id=settings.model_id,
                tts_voice=settings.voice,
                tts_speed=settings.speed,
                tts_basis_digest=None,
            )

        committed_preparation = preparation
        committed_episode = episode
        committed_basis = basis
        pm = get_project_manager()
        committed_version: int | None = None

        def _register_basis() -> None:
            register_narration_audio_transactionally(
                project_path=project_path,
                episode=committed_episode,
                preparation=committed_preparation,
                settings=settings,
            )

        def _activate(_script_path: Path) -> None:
            nonlocal committed_version, prior_manifest_captured, prior_manifest_entry
            manifest_adapter = ProjectArtifactManifestAdapter(project_path)
            prior_manifest_entry = manifest_adapter.get_entry(ArtifactKey.episode_audio(committed_episode, resource_id))
            prior_manifest_captured = True
            committed_version = generator.versions.commit_staged_version(
                resource_type="audio",
                resource_id=resource_id,
                prompt=text,
                staged_file=staged_path,
                current_file=output_path,
                on_commit=_register_basis,
                tts_provider_id=settings.provider_id,
                tts_model_id=settings.model_id,
                tts_voice=settings.voice,
                tts_speed=settings.speed,
                tts_basis_digest=committed_basis.digest,
            )

        def _same_script(_project: dict) -> str:
            entry = find_episode(_project, committed_episode)
            current_binding = entry.get("script_file") if isinstance(entry, dict) else None
            if current_binding is None and not (_project.get("episodes") or []):
                return str(script_file)
            if not isinstance(current_binding, str) or (
                ProjectManager.normalize_script_filename(current_binding)
                != ProjectManager.normalize_script_filename(str(script_file))
            ):
                raise EpisodeScriptReboundError(f"episode {committed_episode} script binding changed before TTS commit")
            return current_binding

        with pm.locked_episode_script(
            project_name,
            _same_script,
            validate=False,
            on_commit=_activate,
        ) as current_script:
            items, id_field, current_kind = _resolve_tts_task_items(
                current_script,
                reference_video_route=reference_video_route,
            )
            item = next(
                (
                    candidate
                    for candidate in items
                    if isinstance(candidate, dict) and str(candidate.get(id_field)) == str(resource_id)
                ),
                None,
            )
            if item is None:
                raise ValueError(f"segment not found: {resource_id}")
            current_admission = admit_script_unit(current_kind, item)
            try:
                current_basis = build_narration_audio_basis(current_admission.preparation, settings)
            except ValueError as exc:
                raise RuntimeError("narration changed before TTS commit") from exc
            if current_basis.digest != committed_basis.digest:
                raise RuntimeError("narration changed before TTS commit")
            assets = item.get("generated_assets")
            prior_narration_audio = (
                copy.deepcopy(assets.get("narration_audio", missing_narration_audio))
                if isinstance(assets, dict)
                else missing_narration_audio
            )
            if not isinstance(assets, dict):
                assets = ProjectManager.create_generated_assets(str(current_script.get("content_mode") or "narration"))
                item["generated_assets"] = assets
            assets["narration_audio"] = audio_rel
            pm.update_scene_status(item)

        if committed_version is None:
            raise RuntimeError("TTS commit completed without a version")
        return committed_version

    output_path, version = await generator.generate_audio_async(
        text=text,
        resource_id=resource_id,
        voice=voice,
        speed=speed,
        before_commit=_measure_staged,
        commit_staged=_commit_staged,
        tts_provider_id=settings.provider_id,
        tts_model_id=settings.model_id,
        tts_voice=settings.voice,
        tts_speed=settings.speed,
        tts_basis_digest=basis.digest if basis is not None else None,
    )

    try:
        records = generator.versions.get_versions("audio", resource_id)["versions"]
        created_at = next(
            (record.get("created_at") for record in reversed(records) if record.get("version") == version),
            records[-1].get("created_at") if records else None,
        )
    except Exception:
        logger.warning("读取 TTS 版本入库时间失败 resource_id=%s", resource_id, exc_info=True)
        created_at = None

    result = {
        "version": version,
        "file_path": audio_rel,
        "created_at": created_at,
        "resource_type": "audio",
        "resource_id": resource_id,
        "duration_seconds": duration_seconds,
        "tts_basis_digest": basis.digest if basis is not None else None,
    }
    if task_id is None:
        return result

    def _compensate_cancelled_tts() -> None:
        def _reject_with_manifest_restore() -> None:
            def _restore_manifest() -> None:
                if not prior_manifest_captured or episode is None or basis is None:
                    return
                adapter = ProjectArtifactManifestAdapter(project_path)
                key = ArtifactKey.episode_audio(episode, resource_id)
                expected = ArtifactManifestEntry(artifact_path=audio_rel, basis_digest=basis.digest)
                if adapter.get_entry(key) != expected:
                    raise RuntimeError("current TTS basis changed before cancellation compensation")
                if prior_manifest_entry is None:
                    adapter.delete_entry(key)
                else:
                    adapter.put_entry(key, prior_manifest_entry)

            restored = generator.versions.reject_current_version(
                "audio",
                resource_id,
                rejected_version=version,
                current_file=output_path,
                on_reject=_restore_manifest,
            )
            if not restored:
                raise RuntimeError("current TTS version changed before cancellation compensation")

        if script_file is None or preparation is None or episode is None or basis is None:
            _reject_with_manifest_restore()
            return

        pm = get_project_manager()

        def _same_script(_project: dict) -> str:
            entry = find_episode(_project, episode)
            current_binding = entry.get("script_file") if isinstance(entry, dict) else None
            if current_binding is None and not (_project.get("episodes") or []):
                return str(script_file)
            if not isinstance(current_binding, str) or (
                ProjectManager.normalize_script_filename(current_binding)
                != ProjectManager.normalize_script_filename(str(script_file))
            ):
                raise EpisodeScriptReboundError(f"episode {episode} script binding changed before TTS cancellation")
            return current_binding

        with pm.locked_episode_script(
            project_name,
            _same_script,
            validate=False,
            on_commit=lambda _script_path: _reject_with_manifest_restore(),
        ) as current_script:
            items, id_field, _kind = _resolve_tts_task_items(
                current_script,
                reference_video_route=reference_video_route,
            )
            item = next(
                (
                    candidate
                    for candidate in items
                    if isinstance(candidate, dict) and str(candidate.get(id_field)) == str(resource_id)
                ),
                None,
            )
            if item is None:
                raise ValueError(f"segment not found during TTS cancellation: {resource_id}")
            assets = item.get("generated_assets")
            if not isinstance(assets, dict):
                assets = ProjectManager.create_generated_assets(str(current_script.get("content_mode") or "narration"))
                item["generated_assets"] = assets
            if assets.get("narration_audio") != audio_rel:
                raise RuntimeError("narration audio changed before cancellation compensation")
            if prior_narration_audio is missing_narration_audio:
                assets.pop("narration_audio", None)
            else:
                assets["narration_audio"] = copy.deepcopy(prior_narration_audio)
            pm.update_scene_status(item)

    return CompensableGenerationResult(result, cancel_compensation=_compensate_cancelled_tts)


# character_name 经 validate_asset_name 校验合法字符，但不限长度；task_id 固定是 uuid4().hex
# （32 字节 ASCII）。若不裁剪，超长角色名 + 前缀/分隔符/task_id 拼出的 resource_id，
# 再叠加 VersionManager.add_version 的 "_v{n}_{timestamp}{ext}" 版本文件名后缀，
# 可能在 255 字节 NAME_MAX 的文件系统上让落盘/建版本失败。留出足够余量后裁剪角色名部分——
# resource_id 本身不需要人工从文件名反解角色名（仅内部拼接，无解析方），裁剪不影响正确性，
# 唯一性完全靠 task_id 保证。
_SAMPLE_ID_NAME_MAX_BYTES = 80


def _truncate_name_bytes(name: str, max_bytes: int) -> str:
    """按 UTF-8 字节数裁剪，裁剪点落在多字节字符中间时丢弃残缺字符而非产生非法编码。"""
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def voice_sample_resource_id(character_name: str, task_id: str) -> str:
    """角色 TTS 试听样本在 ``audio/`` 下的资源 id（区别于旁白 segment id 命名空间）。

    生成产物只是待确认的预览件，落盘位置与旁白共用 ``audio/`` 目录但用固定前缀隔离，
    不会与说书模式的 segment id 冲突；只有 confirm 步骤才把音频提升为角色 reference_audio。

    带 ``task_id`` 而非只用角色名：同一角色前一次成功样本尚未确认时发起重新生成会产生
    新任务，若资源 id 只按角色名固定，新任务落盘会原地覆盖前一个已成功任务引用的文件——
    旧任务的 ``result.file_path`` 字段不变，但物理内容已变成新任务的（甚至是校验失败前
    写入的）字节；若前一个任务的 task_id 仍被别处持有（如另一浏览器标签页）并调用 confirm，
    会把错误内容误落为角色参考音频。每次生成用任务专属文件名，杜绝跨任务覆盖。
    """
    safe_name = _truncate_name_bytes(character_name, _SAMPLE_ID_NAME_MAX_BYTES)
    return f"voice_sample__{safe_name}__{task_id}"


async def execute_character_voice_sample_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """为角色参考音频候选生成一段 TTS 试听样本（预览用，不写入角色资产）。

    ``resource_id`` 是角色名；文本与音色显式来自 payload（``prompt`` = 待合成文本，
    ``voice`` = 用户选定的音色 id），不回落任何全局旁白配置。生成产物须满足与
    参考音频上传同口径的校验（格式经落盘扩展名固定为 wav、时长 2-10 秒、≤15MB）；
    校验失败直接抛错让任务落 failed，不静默放行不合规样本。
    """
    character_name = validate_asset_name(resource_id)
    text = payload.get("prompt")
    voice = payload.get("voice")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("voice sample 任务需要非空 payload.prompt（待合成文本）")
    if not isinstance(voice, str) or not voice.strip():
        raise ValueError("voice sample 任务需要 payload.voice（音色 id）")
    if not task_id:
        # 恒由 worker 经 execute_generation_task 传入队列任务自身 id；缺失说明调用方绕过了
        # 常规队列执行路径（如误从别处直接调用），而 sample_id 的跨任务隔离依赖它，fail-fast。
        raise ValueError("voice sample 任务需要 task_id")

    project = await asyncio.to_thread(get_project_manager().load_project, project_name)
    if resolve_asset_key(project.get("characters"), character_name) is None:
        # 与 execute_character_task 等其它执行器同口径：入队后、worker 取到任务前角色可能
        # 已被删除，执行前重新核实存在，避免花钱合成一段没有归属的孤儿预览。
        raise ValueError(f"character not found: {character_name}")
    ctx = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        user_id=user_id,
        audio=AudioLaneRequest(),
    )
    generator = ctx.generator

    sample_id = voice_sample_resource_id(character_name, task_id)
    _, version = await generator.generate_audio_async(
        text=text.strip(),
        resource_id=sample_id,
        voice=voice.strip(),
        speed=None,
    )

    audio_rel = resource_relative_path("audio", sample_id)
    audio_abs = get_project_manager().get_project_path(project_name) / audio_rel

    def _read_bytes() -> bytes:
        return audio_abs.read_bytes()

    content = await asyncio.to_thread(_read_bytes)
    if len(content) > AUDIO_REFERENCE_MAX_BYTES:
        raise ValueError(f"生成的语音样本超过 {AUDIO_REFERENCE_MAX_BYTES // (1024 * 1024)}MB 限制")

    duration = await probe_audio_duration_seconds(content, audio_abs.suffix)
    if duration is not None and not (AUDIO_REFERENCE_MIN_SECONDS <= duration <= AUDIO_REFERENCE_MAX_SECONDS):
        raise ValueError(
            f"生成的语音样本时长 {duration:.1f}s 超出 "
            f"{AUDIO_REFERENCE_MIN_SECONDS:.0f}-{AUDIO_REFERENCE_MAX_SECONDS:.0f} 秒范围"
        )

    return {
        "version": version,
        "file_path": audio_rel,
        "resource_type": "audio",
        "resource_id": sample_id,
        "character_name": character_name,
        "voice": voice.strip(),
        "duration_seconds": duration,
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
    requested_visual_prompt = copy.deepcopy(prompt)

    def _load():
        _pm = get_project_manager()
        _project = _pm.load_project(project_name)
        _project_path = _pm.get_project_path(project_name)
        _script = _pm.load_script(project_name, script_file)
        _items, _id_field, _, _, _ = get_storyboard_items(_script)
        _resolved = find_storyboard_item(_items, _id_field, resource_id)
        _item = _resolved[0] if _resolved else {}
        return (
            _project,
            _project_path,
            _item,
            resolve_content_mode(_script, _project),
            resolve_script_kind(_script),
            _script,
        )

    project, project_path, item, content_mode, script_kind, script = await asyncio.to_thread(_load)
    delivery_options = NarrationDeliveryRequestOptions.from_payload(payload)
    # lane 归桶按项目路线求值，与提交入口（``generate_video``）同源：入口挡掉参考路线后
    # 到达这里的项目恒为 i2v，但桶不在两处各硬编码一次，避免路线口径分叉。
    ctx = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        user_id=user_id,
        video=VideoLaneRequest(capability=video_bucket_for_generation_mode(project.get("generation_mode"))),
        audio=AudioLaneRequest() if delivery_options.narration_delivery == USE_TTS else None,
    )
    generator = ctx.generator
    registry_provider_id = ctx.video.provider_model.provider_id
    model_name = ctx.video.backend_model
    supported_durations: list[int] = list(ctx.video.supported_durations)
    resolution = ctx.video.resolution

    storyboard_file, end_image = resolve_storyboard_video_inputs(
        project_path=project_path,
        resource_id=resource_id,
        item=item,
    )
    aspect_ratio = get_aspect_ratio(project, "videos")
    seed = payload.get("seed")
    visual_basis_digest = (
        await asyncio.to_thread(
            build_storyboard_video_visual_basis,
            prompt=requested_visual_prompt,
            storyboard_image=storyboard_file,
            end_frame_image=end_image,
            aspect_ratio=aspect_ratio,
            provider_id=registry_provider_id,
            model_id=model_name,
            resolution=resolution,
            seed=seed,
            content_mode=content_mode,
            utterances=item.get("utterances") if content_mode == "drama" else None,
            has_utterances=content_mode == "drama" and "utterances" in item,
            voice_characters=(None if ctx.video.is_silent else project.get("characters"))
            if content_mode == "drama"
            else None,
        )
    ).digest

    # Voice_Profiles 声明段唯一来源是下方 build_drama_video_prompt 的机械派生：调用方（WebUI
    # 请求体 / 剧本 JSON 残留）自带的 voice_profiles 一律先剥离，不因 utterances 门控不触发
    # （narration/ad、或 drama 无 utterances 的条目）而绕过 C 类（真无声）门控直达 YAML。
    if isinstance(prompt, dict):
        prompt = strip_voice_profiles(prompt)

    # drama 口型台词单一真相源在场景级有序 utterances：从 dialogue-kind 条目取台词注入 video YAML
    # 的 dialogue 出口（覆盖 payload 里 drama 已不再携带的 video_prompt.dialogue）。narration / ad
    # 的 item 无 utterances 字段，payload.dialogue 原样透传；SDK 路径 prompt 已是渲染好的字符串、跳过。
    if isinstance(item, dict) and isinstance(prompt, dict) and content_mode == "drama":
        # 无声（C 类模型不产音、或本集关闭音频）传 characters=None 即不注入 Voice_Profiles；
        # 有音轨模型（含恒有声、开关不可控的型号）机械派生角色声音风格。
        # 两条无声路径同口径，判据落在 VideoLaneResult.is_silent。台词不受影响、照常下发。
        voice_characters = None if ctx.video.is_silent else (project.get("characters") or {})
        if "utterances" in item:
            prompt = build_drama_video_prompt(prompt, item.get("utterances"), characters=voice_characters)
        else:
            # utterances 迁移前的存量剧本：load_script 按原始 JSON 读盘不过 pydantic，不会
            # 被 DramaScene._migrate_legacy 自动补齐，台词仍留在 video_prompt.dialogue。
            prompt = build_drama_video_prompt_from_legacy_dialogue(prompt, characters=voice_characters)

    prompt_text = _normalize_video_prompt(prompt)
    service_tier = payload.get("video_provider_settings", {}).get("service_tier", "default")

    # provider / model / 能力 / 分辨率均取自单次解析的 video lane：能力按 backend 实际身份
    # （registry provider_id + backend.model）查询，与实际要调用的 model 对齐——历史任务 payload
    # 携带 provider 覆盖、或自定义供应商目标 model 被禁用回退时，二者一致避免 duration 守卫误判
    # （用「项目默认 model 的能力」误判「实际调用的 model」）。能力不可解析时 supported_durations
    # 留空，守卫遇空列表放行（不更坏，见 ADR-0002）。解析/构造失败已在 resolve_generation_context
    # 内原样上抛整次任务失败，不再有硬编码 provider/model 静默兜底。
    # duration 解析收口于执行层：payload > project.default_duration > caps 默认。
    # 用 ``is not None`` 而非 ``or`` 取 payload 值，避免显式 falsy 值被当作未设置。
    duration_seconds = payload.get("duration_seconds")
    if duration_seconds is None:
        duration_seconds = project.get("default_duration")
    if not duration_seconds:
        # 取首项前先按当前分辨率的联动约束收窄：否则 Veo + 1080p/4k 的默认（Auto）设置会取到
        # 4 秒，被 backend 的「该分辨率必须 8 秒」拒绝——UI 已按同一份声明门控，此处不收窄
        # 就等于默认配置必然失败。显式指定的时长不经此收窄，其合法性由 assert_duration_supported
        # 与 backend 的执行期校验把关。
        candidates = constrain_durations(registry_provider_id, model_name, supported_durations, resolution=resolution)
        duration_seconds = (
            candidates[0] if candidates else _get_model_default_duration(registry_provider_id, model_name)
        )

    delivery_projection = None
    if delivery_options.narration_delivery == USE_TTS:
        episode = ProjectManager.resolve_episode_from_script(script, str(script_file))
        current_planned_duration = item.get("duration_seconds") if isinstance(item, dict) else None
        if (
            not isinstance(current_planned_duration, int)
            or isinstance(current_planned_duration, bool)
            or current_planned_duration <= 0
        ):
            current_planned_duration = project.get("default_duration")
        if (
            not isinstance(current_planned_duration, int)
            or isinstance(current_planned_duration, bool)
            or current_planned_duration <= 0
        ):
            candidates = constrain_durations(
                registry_provider_id,
                model_name,
                supported_durations,
                resolution=resolution,
            )
            if not candidates:
                raise ValueError("TTS video request requires a current integer planned duration")
            current_planned_duration = candidates[0]
        constrained_durations = constrain_durations(
            registry_provider_id,
            model_name,
            supported_durations,
            resolution=resolution,
        )
        delivery_projection = await prepare_current_narrated_video_duration(
            project=project,
            episode=episode,
            preparation=admit_script_unit(script_kind, item).preparation,
            project_path=project_path,
            delivery=delivery_options.narration_delivery,
            planned_duration_seconds=current_planned_duration,
            supported_durations=constrained_durations,
            confirmed_request_duration_seconds=delivery_options.confirmed_request_duration_seconds,
            resolver=ResolvedTtsSettingsResolver.from_audio_lane(ctx.audio),
            tts_in_progress=await tts_task_in_progress(
                project_name=project_name,
                resource_id=resource_id,
                script_file=str(script_file),
            ),
        )
        narration_actual_duration = delivery_projection.narration.actual_duration_seconds
        current_visual_duration = (
            await current_selected_video_tier(
                project_path=project_path,
                versions=generator.versions,
                item=item,
                resource_type="videos",
                resource_id=resource_id,
                minimum_actual_duration_seconds=narration_actual_duration,
                visual_basis_digest=visual_basis_digest,
            )
            if narration_actual_duration is not None
            else None
        )
        delivery_projection = prepare_narrated_video_duration(
            narration=delivery_projection.narration,
            planned_duration_seconds=current_planned_duration,
            supported_durations=constrained_durations,
            confirmed_request_duration_seconds=delivery_options.confirmed_request_duration_seconds,
            current_visual_duration_seconds=current_visual_duration,
        )
        if not delivery_projection.allowed:
            raise NarratedVideoDurationBlockedError(delivery_projection)
        request_duration = delivery_projection.request_duration_seconds
        if request_duration is None:
            raise RuntimeError("allowed narrated video projection is missing a request duration")
        duration_seconds = request_duration
    if not isinstance(duration_seconds, (int, str)) or isinstance(duration_seconds, bool):
        raise ValueError("video request duration must be an integer or integer string")
    # 能力守卫：provider 解析之后的唯一权威家（见 ADR-0001）。安全解析交给守卫，
    # 此处不预先 int() 截断，避免把非整数秒静默修正成「碰巧合法」的值。
    assert_duration_supported(duration_seconds, supported_durations)

    if delivery_projection is not None:
        if not isinstance(duration_seconds, int) or isinstance(duration_seconds, bool):
            raise RuntimeError("allowed TTS video projection produced a non-integer request duration")
        narration_actual_duration = delivery_projection.narration.actual_duration_seconds
        if narration_actual_duration is None:
            raise RuntimeError("allowed TTS video projection is missing actual narration duration")
        reused = await reuse_current_video_for_tier(
            project_path=project_path,
            versions=generator.versions,
            item=item,
            resource_type="videos",
            resource_id=resource_id,
            request_duration_seconds=duration_seconds,
            minimum_actual_duration_seconds=narration_actual_duration,
            visual_basis_digest=visual_basis_digest,
        )
        if reused is not None:
            return reused

    output_path, version, _, video_uri = await generator.generate_video_async(
        prompt=prompt_text,
        resource_type="videos",
        resource_id=resource_id,
        start_image=storyboard_file,
        end_image=end_image,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        resolution=resolution,
        task_id=task_id,
        seed=seed,
        service_tier=service_tier,
        visual_basis_digest=visual_basis_digest,
    )

    if delivery_projection is not None:
        if not isinstance(duration_seconds, int) or isinstance(duration_seconds, bool):
            raise RuntimeError("allowed TTS video projection produced a non-integer request duration")
        await require_generated_video_covers_current_tts(
            project_name=project_name,
            script_file=str(script_file),
            request_duration_seconds=duration_seconds,
            output_path=output_path,
            versions=generator.versions,
            resource_type="videos",
            resource_id=resource_id,
            version=version,
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
        _char_key = resolve_asset_key(_project.get("characters"), resource_id)
        if _char_key is None:
            raise ValueError(f"character not found: {resource_id}")
        _char_data = _project["characters"][_char_key]
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

    ctx = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        user_id=user_id,
        image=ImageLaneRequest(capability="i2i" if _needs_i2i else "t2i"),
    )
    generator = ctx.generator
    aspect_ratio = get_aspect_ratio(project, "characters")
    image_size = ctx.image.resolution

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
            key = resolve_asset_key(p.get("characters"), resource_id)
            if key is None:
                raise KeyError(f"角色 '{resource_id}' 不存在")
            p["characters"][key]["character_sheet"] = sheet_path

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
    entry = normalize_asset_bucket(project.get("products")).get(normalize_asset_name(resource_id)) or {}
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

    ctx = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        user_id=user_id,
        image=ImageLaneRequest(capability="i2i" if needs_i2i else "t2i"),
    )
    generator = ctx.generator
    aspect_ratio = get_aspect_ratio(project, bucket_key)
    image_size = ctx.image.resolution

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

    characters = normalize_asset_bucket(project.get("characters"))
    project_scenes = normalize_asset_bucket(project.get("scenes"))
    project_props = normalize_asset_bucket(project.get("props"))

    seen: set[str] = set()
    paths: list[Path] = []
    metadata: list[dict] = []
    max_count = 6

    for item in matched_items:
        for char_name in item.get(char_field) or []:
            if not isinstance(char_name, str):
                continue
            char_data = characters.get(normalize_asset_name(char_name))
            sheet = char_data.get("character_sheet") if isinstance(char_data, dict) else None
            if isinstance(sheet, str) and sheet and sheet not in seen:
                p = project_path / sheet
                if p.exists():
                    paths.append(p)
                    seen.add(sheet)
                    metadata.append({"path": sheet, "name": char_name, "ref_type": "character"})
        for scene_name in item.get(scene_field) or []:
            if not isinstance(scene_name, str):
                continue
            scene_data = project_scenes.get(normalize_asset_name(scene_name))
            sheet = scene_data.get("scene_sheet") if isinstance(scene_data, dict) else None
            if isinstance(sheet, str) and sheet and sheet not in seen:
                p = project_path / sheet
                if p.exists():
                    paths.append(p)
                    seen.add(sheet)
                    metadata.append({"path": sheet, "name": scene_name, "ref_type": "scene"})
        for prop_name in item.get(prop_field) or []:
            if not isinstance(prop_name, str):
                continue
            prop_data = project_props.get(normalize_asset_name(prop_name))
            sheet = prop_data.get("prop_sheet") if isinstance(prop_data, dict) else None
            if isinstance(sheet, str) and sheet and sheet not in seen:
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
    """Execute a grid joint-image generation task.

    resource_id is the grid_id. Steps:
    1. Load GridGeneration, set status to generating
    2. Generate the joint image via MediaGenerator (versioned as resource_type "grids")
    3. Mark completed

    只产出联合图，不触碰任何分镜格；落格由独立的切分操作
    （server.services.grid_split.apply_grid_split）显式执行。
    """
    from lib.grid.layout import GRID_FALLBACK_RESOLUTION
    from lib.grid_manager import GridManager

    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    grid_manager = GridManager(project_path)

    # a) Load grid
    grid = grid_manager.get(resource_id)
    if grid is None:
        raise ValueError(f"grid not found: {resource_id}")

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
        project = await asyncio.to_thread(get_project_manager().load_project, project_name)
        ctx = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            user_id=user_id,
            image=ImageLaneRequest(capability="i2i" if _needs_i2i else "t2i"),
        )
        generator = ctx.generator
        aspect_ratio = payload.get("grid_aspect_ratio") or get_aspect_ratio(project, "storyboards")

        # 回填 grid metadata：route 层创建/重建时无法预知 needs_i2i，由此处补齐。
        # provider 记 registry 身份（供后续重解析定位供应商），model 记 backend 实际身份
        # （自定义供应商目标 model 被禁用回退时，实际调用的 model 与解析出的 model_id 不同）。
        grid.provider = ctx.image.provider_model.provider_id
        grid.model = ctx.image.backend_model
        grid_manager.save(grid)
        # 保底档与档位门控（``large_grid_allowed``）取同一常量，避免门控按 2K 判定、
        # 渲染却按别的档位下发
        image_size = ctx.image.resolution or GRID_FALLBACK_RESOLUTION

        _image_path, version = await generator.generate_image_async(
            prompt=prompt_text,
            resource_type="grids",
            resource_id=resource_id,
            reference_images=reference_images,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
        )

        # e) Mark joint image ready；联合图内容已更新，旧的落格结果不再对应当前图，
        # split_at 清空表示「待显式切分」。
        grid.grid_image_path = f"grids/{resource_id}.png"
        grid.status = "completed"
        grid.split_at = None
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
    claimed_provider_id: str | None = None,
) -> dict[str, Any]:
    """Lazy proxy to avoid circular import: reference_video_tasks imports from this module."""
    from server.services.reference_video_tasks import execute_reference_video_task

    return await execute_reference_video_task(
        project_name,
        resource_id,
        payload,
        user_id=user_id,
        task_id=task_id,
        claimed_provider_id=claimed_provider_id,
    )


async def _execute_image_edit_task_proxy(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Lazy proxy to avoid circular import: image_edit_tasks imports from this module."""
    from server.services.image_edit_tasks import execute_image_edit_task

    return await execute_image_edit_task(project_name, resource_id, payload, user_id=user_id, task_id=task_id)


_TASK_EXECUTORS = {
    "storyboard": execute_storyboard_task,
    "video": execute_video_task,
    "tts": execute_tts_task,
    "voice_sample": execute_character_voice_sample_task,
    "character": execute_character_task,
    "scene": execute_scene_task,
    "prop": execute_prop_task,
    "product": execute_product_task,
    "grid": execute_grid_task,
    "reference_video": _execute_reference_video_task_proxy,
    "image_edit": _execute_image_edit_task_proxy,
}


async def execute_generation_task(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
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
        # 能力类异常（Image/VideoCapabilityError、ReferencePayloadFloorError）原样上抛：
        # worker 的 _encode_task_failure_message 按 code + params 落库，渲染留到读侧
        # Translator，同一失败任务按 Accept-Language 显示 zh/en/vi。
        if task_type == "reference_video":
            result = await _execute_reference_video_task_proxy(
                project_name,
                resource_id,
                payload,
                user_id=user_id,
                task_id=queue_task_id,
                claimed_provider_id=claimed_provider_id,
            )
        else:
            result = await executor(project_name, resource_id, payload, user_id=user_id, task_id=queue_task_id)
        emit_generation_success_batch(
            task_type=task_type,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
        )
        return result
