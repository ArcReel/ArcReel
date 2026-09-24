"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    ArtifactInputClaim,
    active_artifact_currency_resolver,
    assert_current_artifact_input_claims_usable,
    bind_artifact_input_claims_to_content_digests,
    bind_artifact_input_claims_to_frozen_visuals,
    resolve_usable_episode_script_input,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifacts.artifact_manifest import (
    ArtifactBasisDescriptor,
    ArtifactKey,
    compose_video_artifact_basis,
)
from lib.artifacts.generation_input import (
    AssetSheetInput,
    FrozenGenerationInput,
    InputRefused,
    StoryboardImageInput,
    asset_sheet_input,
    grid_references,
    project_input_observation,
    storyboard_image_input,
)
from lib.artifacts.image_reference_snapshot import FrozenImageReferences, freeze_image_references
from lib.artifacts.version_manager import PaidVersionCommit
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.video_visual_provenance import build_storyboard_video_visual_basis
from lib.artifacts.visual_artifact_provenance import (
    GridStoryboardVisual,
    build_grid_composite_visual_basis,
    build_storyboard_video_artifact_visual_basis,
    project_basis_style_description,
)
from lib.backends.video_backend_contract import VideoCapabilityError
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import video_bucket_for_generation_mode
from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_queue import (
    DispatchProviderChanged,
    get_generation_queue,
    without_video_execution_identity,
)
from lib.generation.video_request_facts import (
    VideoRequestFactsError,
    audio_switch_conflict,
    require_video_request_facts,
)
from lib.infra.api_errors import ConflictError
from lib.infra.async_thread import EventLoopBridge, run_noninterruptible_sync
from lib.infra.path_safety import safe_join, try_safe_join
from lib.infra.schema_guards import is_int
from lib.infra.thumbnail import extract_video_thumbnail
from lib.project.asset_derivatives import DERIVATIVE_ASSET_TYPE, DERIVATIVE_TASK_TYPE
from lib.project.asset_types import (
    ASSET_SPECS,
    resolve_asset_key,
    validate_asset_name,
)
from lib.project.project_change_hints import build_change_label, emit_project_change_batch, project_change_source
from lib.project.project_manager import (
    EpisodeScriptReboundError,
    ProjectManager,
    get_project_manager,
    is_reference_video_project,
    resolve_episode_script_binding,
)
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_relative_path
from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.prompt_utils import render_storyboard_video_prompt
from lib.prompts.reference_image_numbering import clamp_reference_images
from lib.script.reference_video.execution_checkpoint import (
    NarrationExecutionFacts,
    ProviderMediaInput,
    StagedProviderMedia,
    StoryboardSubmissionCheckpoint,
    checkpoint_version_metadata,
    cleanup_staged_provider_media,
    stage_provider_media_for_task,
)
from lib.script.script_editor import resolve_items
from lib.script.script_models import resolve_content_mode
from lib.script.script_skeleton import SKELETON_ENTITY_TYPES, SKELETON_ITEM_LABEL_KEYS, resolve_script_kind
from lib.script.storyboard_sequence import (
    find_storyboard_item,
    get_storyboard_items,
)
from lib.speech.audio_utils import (
    AUDIO_REFERENCE_MAX_BYTES,
    AUDIO_REFERENCE_MAX_SECONDS,
    AUDIO_REFERENCE_MIN_SECONDS,
    probe_audio_duration_seconds,
    probe_existing_audio_duration_seconds,
)
from lib.speech.narration_delivery import (
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
from lib.speech.speech_artifact_provenance import build_video_duration_basis
from lib.speech.speech_composition import SpeechAdmissionError, admit_script_unit
from server.services.admission.reference_admission import input_refusal_error
from server.services.currency.video_artifact_currency import (
    VideoArtifactCommitter,
    complete_video_artifact_commit,
    freeze_video_speech_facts,
)
from server.services.tasks.derivative_sheet_tasks import execute_character_derivative_task
from server.services.tasks.formal_image_commit import (
    FormalImageCommitOutcome,
    FormalImagePlan,
    StagedImageCommit,
    get_aspect_ratio,
    grid_formal_image_callback,
    run_asset_sheet_image_task,
    run_formal_image_task,
    storyboard_formal_image_callback,
)
from server.services.tasks.generation_context import (
    AudioLaneRequest,
    ImageLaneRequest,
    VideoLaneRequest,
    resolve_generation_context,
)
from server.services.tasks.image_edit_tasks import execute_image_edit_task
from server.services.tasks.narration_delivery_tasks import (
    CurrentTtsSettingsResolver,
    ResolvedTtsSettingsResolver,
    active_narrated_video_resource_ids,
    current_selected_video_tier,
    reuse_current_video_for_tier,
    storyboard_planning_duration,
    tts_task_in_progress,
)
from server.services.tasks.reference_video_tasks import execute_reference_video_task

logger = logging.getLogger(__name__)


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
    （见 ADR-0001），故能力校验只能坐在 provider 解析之后。``supported_durations`` 为空只在
    时长由端点固定时出现（``docs/adr/0082``），此时放行：能力解析不出已在取事实时阻断。

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
    except (TypeError, ValueError) as exc:
        raise VideoCapabilityError("video_duration_invalid", duration=duration) from exc
    if not numeric.is_integer():
        raise VideoCapabilityError("video_duration_invalid", duration=duration)
    seconds = int(numeric)
    if seconds not in supported_durations:
        raise VideoCapabilityError(
            "video_duration_not_supported",
            duration=seconds,
            supported=", ".join(str(d) for d in supported_durations),
        )


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
            from lib.script.grid.grid_manager import GridManager

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
    elif task_type == DERIVATIVE_TASK_TYPE:
        # 衍生的 resource_id 本身是 `本体名/衍生名`，两段原样成为路径层级。
        sheet_rel = resource_relative_path(CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_id)
        paths.append((sheet_rel, project_path / sheet_rel))

    result: dict[str, int] = {}
    for rel, abs_path in paths:
        if abs_path.exists():
            result[rel] = abs_path.stat().st_mtime_ns

    return result


# (entity_type, action, label_key, include_script_episode)
# label_key 是事件载荷携带的稳定标识，界面按用户语言查表成文；文案本身见 lib/i18n 的
# ``event_label_*``。三类项目级资产（character / scene / prop）的 spec 由
# lib.project.asset_types.ASSET_SPECS 派生。
# storyboard / video / reference_video 不在此表——三者按剧本骨架种类（segments/scenes/shots/
# video_units）动态派生 entity_type 与条目名词，见 _SKELETON_DRIVEN_TASK_ACTIONS，避免恒发
# ``segment``/「分镜」而与分镜级事件（project_state_projection.py）名词不一致。
_TASK_CHANGE_SPECS: dict[str, tuple] = {
    "grid": ("grid", "grid_ready", "grid", True),
    "grid_split": ("grid", "grid_split_done", "grid_split", True),
    "voice_sample": ("character", "voice_sample_ready", "voice_sample", False),
    **{atype: (atype, "updated", f"asset_image_{atype}", False) for atype in ASSET_SPECS},
    # 衍生资产图落在本体的 entity 下（entity_id 是 `本体名/衍生名`），但条目名词另立一条：
    # 复用 asset_image_character 会把复合 id 当成角色名读出来。
    DERIVATIVE_TASK_TYPE: (DERIVATIVE_ASSET_TYPE, "updated", "asset_image_character_derivative", False),
}

# 骨架驱动的任务类型 → 完成事件 action。entity_type/条目名词按项目剧本当前骨架种类
# （resolve_script_kind，与分镜级事件同一判定）动态解析，不按 task_type 恒定硬编码。
_SKELETON_DRIVEN_TASK_ACTIONS: dict[str, str] = {
    "storyboard": "storyboard_ready",
    "video": "video_ready",
    "reference_video": "reference_video_ready",
    "tts": "tts_ready",
}

# 任务类型自带条目标签的例外：tts 的产物是旁白配音，与骨架条目名词不同名。reference_video 显式
# 指向视频单元，与参考生视频项目的骨架名词同口径；storyboard/video 未列出，回退到按骨架种类派生
# 的 label_key（分镜），与同项目分镜级事件同口径。
_SKELETON_TASK_LABEL_KEYS: dict[str, str] = {
    "reference_video": "skeleton_video_units",
    "tts": "narration_audio",
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
        reference_route_task = task_type == "reference_video"
        if task_type == "tts":
            try:
                reference_route_task = is_reference_video_project(get_project_manager().load_project(project_name))
            except Exception:
                reference_route_task = False
        if reference_route_task:
            # 参考生视频的资源身份恒为 video unit；生成模式来自创建后不可变的 project.json，
            # 不让 ad 剧本残留的 shots[] 在 TTS 成功后把 E1U* 事件错分为 shot。
            kind = "video_units"
        else:
            kind = resolve_script_kind(script) if isinstance(script, dict) else "segments"
        entity_type = SKELETON_ENTITY_TYPES.get(kind, "segment")
        label_key = _SKELETON_TASK_LABEL_KEYS.get(task_type) or SKELETON_ITEM_LABEL_KEYS.get(kind, "skeleton_segments")
        include_script_episode = True
    else:
        spec = _TASK_CHANGE_SPECS.get(task_type)
        if spec is None:
            return {}
        entity_type, action, label_key, include_script_episode = spec

    asset_fingerprints = compute_affected_fingerprints(project_name, task_type, resource_id)

    change: dict[str, Any] = {
        "entity_type": entity_type,
        "action": action,
        "entity_id": resource_id,
        **build_change_label(label_key, id=resource_id),
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


@dataclass(frozen=True, slots=True)
class _StoryboardImageInputs:
    """分镜图任务在解析 image lane 之前就能备齐的输入：项目快照、剧本 claim 与成立的生成输入。"""

    project: dict[str, Any]
    project_path: Path
    currency_resolver: ArtifactCurrencyResolver
    script_claim: ArtifactInputClaim
    generation_input: StoryboardImageInput


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

    if payload.get("prompt") is None:
        raise ValueError("prompt is required for storyboard task")

    def _load() -> _StoryboardImageInputs:
        _project = get_project_manager().load_project(project_name)
        _project_path = get_project_manager().get_project_path(project_name)
        _script = get_project_manager().load_script(project_name, script_file)
        _script_input = resolve_usable_episode_script_input(
            project_path=_project_path,
            project=_project,
            script=_script,
            script_filename=str(script_file),
        )
        _currency_resolver = active_artifact_currency_resolver(_project_path, _project)
        _generation_input = storyboard_image_input(
            _project,
            _script,
            episode=_script_input.episode,
            resource_id=resource_id,
            observation=project_input_observation(_project_path),
        )
        # 生成输入不成立时在解析供应商通道与付费之前失败，一次报出全部缺口。
        if isinstance(_generation_input, InputRefused):
            raise input_refusal_error(_generation_input)
        return _StoryboardImageInputs(
            project=_project,
            project_path=_project_path,
            currency_resolver=_currency_resolver,
            script_claim=_script_input.claim,
            generation_input=_generation_input,
        )

    inputs = await asyncio.to_thread(_load)
    project, project_path = inputs.project, inputs.project_path
    # 参考图先于提示词定型：backend 的参考图上限决定实际发出几张，编号只能按裁剪后的序列渲染，
    # 故 image lane 在此解析一次并沿用到提交，避免重解析落到上限不同的 backend。
    context = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        project_path=project_path,
        user_id=user_id,
        image=ImageLaneRequest(generation_type="i2i" if inputs.generation_input.references else "t2i"),
    )

    def _bind_claims(
        claims: Sequence[ArtifactInputClaim], content_digests: Mapping[str, str]
    ) -> tuple[ArtifactInputClaim, ...]:
        return bind_artifact_input_claims_to_content_digests(
            resolver=inputs.currency_resolver,
            claims=claims,
            content_digests=content_digests,
        )

    def _freeze() -> FrozenGenerationInput:
        return inputs.generation_input.freeze(
            max_reference_images=context.image.max_reference_images,
            model=context.image.backend_model,
            bind_claims=_bind_claims,
        )

    artifact_path = f"storyboards/scene_{resource_id}.png"
    with await asyncio.to_thread(_freeze) as frozen:
        # 剧本 claim 排在最前，其后是参考图按冻结字节绑定的 claims。
        formal_claims = (inputs.script_claim, *frozen.claims)

        async def _assert_claims_usable() -> None:
            await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

        async def _pre_submit(_generator: Any) -> None:
            await _assert_claims_usable()

        def _build_commit(generator: Any, outcome_box: list[FormalImageCommitOutcome]) -> StagedImageCommit:
            return storyboard_formal_image_callback(
                project_name=project_name,
                script_file=str(script_file),
                resource_id=resource_id,
                artifact_path=artifact_path,
                prompt=frozen.prompt,
                versions=generator.versions,
                task_id=task_id,
                basis=frozen.basis,
                outcome_box=outcome_box,
                project_manager=get_project_manager(),
            )

        return await run_formal_image_task(
            project_name=project_name,
            payload=payload,
            project=project,
            user_id=user_id,
            task_id=task_id,
            frozen_references=frozen.references,
            context=context,
            plan=FormalImagePlan(
                resource_type="storyboards",
                resource_id=resource_id,
                artifact_path=artifact_path,
                prompt=frozen.prompt,
                aspect_ratio=inputs.generation_input.canvas_ratio,
                build_commit_callback=_build_commit,
                pre_submit=_pre_submit,
                before_submit=_assert_claims_usable,
                warnings=frozen.warnings,
            ),
        )


def _resolve_tts_task_items(
    script: dict[str, Any],
    *,
    reference_video_route: bool,
) -> tuple[list[Any], str, str]:
    """Resolve TTS units from the generation route fixed at task start."""

    if not reference_video_route:
        return resolve_items(script)
    # 参考生视频的骨架种类由任务开工时定死的生成模式给出，直接指定；取证解析只服务于生成模式未知的调用方。
    return resolve_items(script, kind="video_units")


async def execute_tts_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """为一个 narrator-owned script unit 合成独立旁白配音。"""
    script_file = payload.get("script_file")

    def _prepare() -> tuple[dict, Path, str, Any | None, int | None, bool, tuple[ArtifactInputClaim, ...]]:
        pm = get_project_manager()
        current_project = pm.load_project(project_name)
        reference_video_route = is_reference_video_project(current_project)
        project_path = pm.get_project_path(project_name)
        if script_file:
            script = pm.load_script(project_name, script_file)
            script_input = resolve_usable_episode_script_input(
                project_path=project_path,
                project=current_project,
                script=script,
                script_filename=str(script_file),
            )
            episode = script_input.episode
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
            return (
                current_project,
                project_path,
                text,
                admission.preparation,
                episode,
                reference_video_route,
                (script_input.claim,),
            )

        legacy_text = payload.get("text") or payload.get("prompt")
        if not isinstance(legacy_text, str) or not legacy_text.strip():
            raise ValueError("tts task 需要 payload.text 或 payload.script_file 之一")
        return current_project, project_path, legacy_text.strip(), None, None, reference_video_route, ()

    (
        project,
        project_path,
        text,
        preparation,
        episode,
        reference_video_route,
        formal_input_claims,
    ) = await asyncio.to_thread(_prepare)

    if isinstance(script_file, str) and resource_id in await active_narrated_video_resource_ids(
        project_name=project_name,
        resource_ids=(resource_id,),
        script_file=script_file,
        user_id=user_id,
    ):
        raise ConflictError("tts_conflicts_with_active_narrated_video", resource_id=resource_id)

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
    # 选片依据的解析失败在回调里发生、在外层消费，用单元素信箱传递而非 nonlocal 哨兵。
    tts_selection_errors: list[BaseException] = []
    tts_settings_bridge = EventLoopBridge.capture()
    selected_current = True

    class _TtsSelectionResolutionFailed(RuntimeError):
        pass

    async def _measure_staged(staged_path: Path) -> None:
        nonlocal duration_seconds
        try:
            measured_duration = await probe_existing_audio_duration_seconds(staged_path)
        except (Exception, asyncio.CancelledError) as exc:
            tts_selection_errors.append(exc)
            return
        if measured_duration is None or not math.isfinite(measured_duration) or measured_duration <= 0:
            tts_selection_errors.append(RuntimeError("generated narration audio duration is unavailable"))
            return
        duration_seconds = float(measured_duration)

    def _commit_staged(staged_path: Path, output_path: Path) -> int | PaidVersionCommit:
        nonlocal selected_current
        if script_file is None or preparation is None or episode is None or basis is None:
            selected_current = False
            return generator.versions.commit_staged_paid_version(
                resource_type="audio",
                resource_id=resource_id,
                prompt=text,
                staged_file=staged_path,
                current_file=output_path,
                select_current=False,
                tts_provider_id=settings.provider_id,
                tts_model_id=settings.model_id,
                tts_voice=settings.voice,
                tts_speed=settings.speed,
                tts_basis_digest=None,
                tts_actual_duration_seconds=duration_seconds,
            )

        committed_preparation = preparation
        committed_episode = episode
        committed_basis = basis
        pm = get_project_manager()
        committed_outcome: list[PaidVersionCommit] = []
        should_select = False
        guarded_project: list[dict[str, Any]] = []

        version_metadata = {
            "tts_provider_id": settings.provider_id,
            "tts_model_id": settings.model_id,
            "tts_voice": settings.voice,
            "tts_speed": settings.speed,
            "tts_basis_digest": committed_basis.digest,
            "artifact_episode": committed_episode,
            "artifact_audio_basis": ArtifactBasisDescriptor.from_basis(committed_basis).to_dict(),
            "tts_actual_duration_seconds": duration_seconds,
            "execution_script_file": str(script_file),
        }

        def _archive_paid_history() -> PaidVersionCommit:
            return generator.versions.commit_staged_paid_version(
                resource_type="audio",
                resource_id=resource_id,
                prompt=text,
                staged_file=staged_path,
                current_file=output_path,
                select_current=False,
                **version_metadata,
            )

        if tts_selection_errors:
            committed_outcome.append(_archive_paid_history())
            selected_current = False
            return committed_outcome[-1]

        def _register_basis() -> None:
            register_narration_audio_transactionally(
                project_path=project_path,
                episode=committed_episode,
                preparation=committed_preparation,
                settings=settings,
            )

        def _activate(_script_path: Path) -> None:
            committed_outcome.append(
                generator.versions.commit_staged_paid_version(
                    resource_type="audio",
                    resource_id=resource_id,
                    prompt=text,
                    staged_file=staged_path,
                    current_file=output_path,
                    select_current=lambda: should_select,
                    on_select=_register_basis,
                    **version_metadata,
                )
            )

        def _same_script(_project: dict) -> str:
            guarded_project.append(_project)
            current_binding = resolve_episode_script_binding(_project, committed_episode, str(script_file))
            if current_binding is None:
                raise EpisodeScriptReboundError(f"episode {committed_episode} script binding changed before TTS commit")
            return current_binding

        try:
            with pm.locked_episode_script(
                project_name,
                _same_script,
                validate=False,
                on_commit=_activate,
            ) as current_script:
                if not guarded_project:
                    raise RuntimeError("TTS commit guard did not expose the current project")
                try:
                    current_commit_settings = tts_settings_bridge.run(
                        CurrentTtsSettingsResolver(
                            project_name,
                            user_id=user_id,
                            project_path=project_path,
                            context_resolver=resolve_generation_context,
                        ).resolve_tts_synthesis_settings(guarded_project[-1])
                    )
                except (Exception, asyncio.CancelledError) as exc:
                    tts_selection_errors.append(exc)
                    raise _TtsSelectionResolutionFailed from exc
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
                current_basis = None
                if not tts_selection_errors and item is not None:
                    current_admission = admit_script_unit(current_kind, item)
                    try:
                        current_basis = build_narration_audio_basis(
                            current_admission.preparation,
                            current_commit_settings,
                        )
                    except ValueError:
                        current_basis = None
                should_select = current_basis is not None and current_basis.digest == committed_basis.digest
                if should_select:
                    assert item is not None
                    assets = item.get("generated_assets")
                    if not isinstance(assets, dict):
                        assets = ProjectManager.create_generated_assets(
                            str(current_script.get("content_mode") or "narration")
                        )
                        item["generated_assets"] = assets
                    assets["narration_audio"] = audio_rel
                    pm.update_scene_status(item)
        except (EpisodeScriptReboundError, _TtsSelectionResolutionFailed):
            committed_outcome.append(_archive_paid_history())
        except BaseException as failure:
            if staged_path.is_file():
                try:
                    _archive_paid_history()
                except BaseException as archive_failure:
                    failure.add_note(f"paid TTS history archival also failed: {archive_failure}")
            raise

        if not committed_outcome:
            raise RuntimeError("paid TTS commit produced no version record")
        selected_current = committed_outcome[-1].selected
        return committed_outcome[-1]

    async def _before_submit() -> None:
        await asyncio.to_thread(
            assert_current_artifact_input_claims_usable,
            project_path,
            formal_input_claims,
        )

    _output_path, version = await generator.generate_audio_async(
        text=text,
        resource_id=resource_id,
        voice=voice,
        speed=speed,
        task_id=task_id,
        before_submit=_before_submit,
        before_commit=_measure_staged,
        commit_staged=_commit_staged,
        tts_provider_id=settings.provider_id,
        tts_model_id=settings.model_id,
        tts_voice=settings.voice,
        tts_speed=settings.speed,
        tts_basis_digest=basis.digest if basis is not None else None,
    )

    if tts_selection_errors:
        raise tts_selection_errors[-1]

    version_record: dict[str, Any] | None = None
    try:
        records = generator.versions.get_versions("audio", resource_id)["versions"]
        version_record = next(
            (record for record in reversed(records) if record.get("version") == version),
            None,
        )
        created_at = next(
            (record.get("created_at") for record in reversed(records) if record.get("version") == version),
            records[-1].get("created_at") if records else None,
        )
    except Exception:
        logger.warning("读取 TTS 版本入库时间失败 resource_id=%s", resource_id, exc_info=True)
        created_at = None

    return {
        "version": version,
        "file_path": (
            audio_rel if selected_current else version_record.get("file") if isinstance(version_record, dict) else None
        ),
        "created_at": created_at,
        "resource_type": "audio",
        "resource_id": resource_id,
        "duration_seconds": duration_seconds,
        "tts_basis_digest": basis.digest if basis is not None else None,
        "selected_current": selected_current,
    }


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
    不会与旁白/解说的 segment id 冲突；只有 confirm 步骤才把音频提升为角色 reference_audio。

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
        task_id=task_id,
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
    script_file: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
    poll_timeout_seconds: int = DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload_script_file = payload.get("script_file")
    if script_file is None and isinstance(payload_script_file, str):
        script_file = payload_script_file
    if not isinstance(script_file, str) or not script_file:
        raise ValueError("script_file is required for video task")

    def _load():
        _pm = get_project_manager()
        _project = _pm.load_project(project_name)
        _project_path = _pm.get_project_path(project_name)
        _script = _pm.load_script(project_name, script_file)
        _script_input = resolve_usable_episode_script_input(
            project_path=_project_path,
            project=_project,
            script=_script,
            script_filename=script_file,
        )
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
            _script_input,
        )

    project, project_path, item, content_mode, script_kind, _script, script_input = await asyncio.to_thread(_load)
    # Queue execution re-materializes mutable visual intent from the current script unit. Direct/internal callers
    # without a task row retain the request-prompt fallback for compatibility with synchronous service tests.
    current_prompt = item.get("video_prompt") if isinstance(item, dict) else None
    prompt = current_prompt if task_id is not None else payload.get("prompt", current_prompt)
    if prompt is None:
        raise ValueError("current script unit is missing video_prompt")
    requested_visual_prompt = copy.deepcopy(prompt)
    delivery_options = NarrationDeliveryRequestOptions.from_payload(payload)
    # lane 归桶按项目生成模式求值，与提交入口（``generate_video``）同源：入口挡掉参考生视频后
    # 到达这里的项目恒为 i2v，但桶不在两处各硬编码一次，避免生成模式口径分叉。
    execution_payload = without_video_execution_identity(payload) if task_id is not None else payload
    ctx = await resolve_generation_context(
        project_name,
        execution_payload,
        project=project,
        user_id=user_id,
        video=VideoLaneRequest(
            generation_type=video_bucket_for_generation_mode(project.get("generation_mode")),
            route="storyboard",
        ),
        audio=AudioLaneRequest() if delivery_options.narration_delivery == USE_TTS else None,
    )
    generator = ctx.generator
    registry_provider_id = ctx.video.provider_model.provider_id
    if claimed_provider_id is not None and registry_provider_id != claimed_provider_id:
        raise DispatchProviderChanged(
            claimed_provider_id=claimed_provider_id,
            actual_provider_id=registry_provider_id,
        )
    if ctx.video.request_facts is None:
        raise RuntimeError("storyboard video lane is missing its request facts")
    request_facts = require_video_request_facts(ctx.video.request_facts)
    if (conflict := audio_switch_conflict(request_facts)) is not None:
        raise VideoRequestFactsError(conflict)
    requested_generate_audio = request_facts.requested_generate_audio
    model_name = ctx.video.backend_model
    supported_durations = list(request_facts.allowed_durations)
    resolution = request_facts.resolution

    artifact_episode = script_input.episode
    formal_input_claims: list[ArtifactInputClaim] = [script_input.claim]
    currency_resolver = active_artifact_currency_resolver(project_path, project)
    storyboard_file, end_image = resolve_usable_storyboard_video_inputs(
        project_path=project_path,
        project=project,
        episode=artifact_episode,
        resource_id=resource_id,
        item=item,
        resolver=currency_resolver,
        claims=formal_input_claims,
    )
    aspect_ratio = get_aspect_ratio(project, "videos")
    seed = payload.get("seed")

    def _visual_basis_digest_for(storyboard_image: Path, end_frame_image: Path | None) -> str:
        return build_storyboard_video_visual_basis(
            prompt=requested_visual_prompt,
            storyboard_image=storyboard_image,
            end_frame_image=end_frame_image,
            aspect_ratio=aspect_ratio,
            provider_id=registry_provider_id,
            model_id=model_name,
            resolution=resolution,
            seed=seed,
            requested_generate_audio=requested_generate_audio,
            content_mode=content_mode,
            utterances=item.get("utterances") if content_mode == "drama" else None,
            has_utterances=content_mode == "drama" and "utterances" in item,
            voice_characters=(None if ctx.video.is_silent else project.get("characters"))
            if content_mode == "drama"
            else None,
        ).digest

    def _current_visual_basis_digest() -> str:
        return _visual_basis_digest_for(storyboard_file, end_image)

    visual_basis_digest = await asyncio.to_thread(_current_visual_basis_digest)

    # 最终文本的合成收敛在 render_storyboard_video_prompt：voice_profiles 剥离、drama 口型台词
    # 注入（分镜级有序 utterances 为单一真相源）、YAML 渲染与反向约束尾词都在其内完成，预览
    # 接口与批量准入读同一出口。无声（C 类模型不产音、或本集关闭音频）传 characters=None 即不
    # 注入 Voice_Profiles，两条无声路径同口径，判据落在 VideoLaneResult.is_silent。
    prompt_text = render_storyboard_video_prompt(
        prompt,
        item if isinstance(item, dict) else None,
        content_mode=content_mode,
        voice_characters=(None if ctx.video.is_silent else (project.get("characters") or {}))
        if content_mode == "drama"
        else None,
    )
    service_tier = payload.get("video_provider_settings", {}).get("service_tier", "default")

    # provider / model / 能力 / 分辨率均取自单次解析的 video lane：能力按 backend 实际身份
    # （registry provider_id + backend.model）查询，与实际要调用的 model 对齐——历史任务 payload
    # 携带 provider 覆盖、或自定义供应商目标 model 被禁用回退时，二者一致避免 duration 守卫误判
    # （用「项目默认 model 的能力」误判「实际调用的 model」）。解析/构造失败已在
    # resolve_generation_context 内原样上抛整次任务失败。
    # duration 解析收口于执行层：payload > project.default_duration > caps 默认。
    # 用 ``is not None`` 而非 ``or`` 取 payload 值，避免显式 falsy 值被当作未设置。
    duration_seconds = (
        item.get("duration_seconds")
        if task_id is not None and isinstance(item, dict)
        else payload.get("duration_seconds")
    )
    if duration_seconds is None:
        duration_seconds = project.get("default_duration")
    if not duration_seconds:
        if request_facts.allowed_durations:
            duration_seconds = request_facts.allowed_durations[0]
        elif request_facts.duration_endpoint_fixed:
            # 端点固定没有档位可借：与 use_tts 路径取同一个规划基准，两条路径的申请秒数一致。
            duration_seconds = storyboard_planning_duration(request_facts, declared=None, project=project)
        else:
            duration_seconds = _get_model_default_duration(registry_provider_id, model_name)

    delivery_projection = None
    if delivery_options.narration_delivery == USE_TTS:
        episode = artifact_episode
        # 档位与端点固定取自执行侧视频请求事实，与预检只差身份来源。
        assert request_facts is not None
        current_planned_duration = storyboard_planning_duration(
            request_facts,
            declared=item.get("duration_seconds") if isinstance(item, dict) else None,
            project=project,
        )
        constrained_durations = list(request_facts.allowed_durations)
        delivery_projection = await prepare_current_narrated_video_duration(
            project=project,
            episode=episode,
            preparation=admit_script_unit(script_kind, item).preparation,
            project_path=project_path,
            delivery=delivery_options.narration_delivery,
            planned_duration_seconds=current_planned_duration,
            supported_durations=constrained_durations,
            confirmed_request_duration_seconds=delivery_options.confirmed_request_duration_seconds,
            duration_endpoint_fixed=request_facts.duration_endpoint_fixed,
            resolver=ResolvedTtsSettingsResolver.from_audio_lane(ctx.audio),
            tts_in_progress=await tts_task_in_progress(
                project_name=project_name,
                resource_id=resource_id,
                script_file=str(script_file),
                user_id=user_id,
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
            duration_endpoint_fixed=request_facts.duration_endpoint_fixed,
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
    duration_seconds = int(float(duration_seconds))

    if delivery_projection is not None:
        if not is_int(duration_seconds):
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
            revalidate_visual_basis_digest=_current_visual_basis_digest,
        )
        if reused is not None:
            return reused

    provider_start_image = storyboard_file
    provider_end_image = end_image

    async def _admit_before_submit() -> Mapping[str, object] | None:
        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_input_claims)
        return None

    checkpoint_hook: Callable[[], Awaitable[Mapping[str, object] | None]] | None = _admit_before_submit
    staged_media: tuple[StagedProviderMedia, ...] = ()
    if task_id is not None:
        artifact_speech_preparation = admit_script_unit(script_kind, item).preparation
        artifact_speech = freeze_video_speech_facts(
            artifact_speech_preparation,
            characters=project.get("characters"),
            include_voice_styles=not ctx.video.is_silent,
        )
        artifact_duration_basis = build_video_duration_basis(duration_seconds)
        artifact_duration_tiers = tuple(
            sorted(
                {
                    duration_seconds,
                    *request_facts.allowed_durations,
                }
            )
        )
        media_inputs = [
            ProviderMediaInput(
                path=storyboard_file,
                role="start_image",
                logical_type="storyboard",
                logical_name=resource_id,
                kind="first_frame",
            )
        ]
        if end_image is not None:
            media_inputs.append(
                ProviderMediaInput(
                    path=end_image,
                    role="end_image",
                    logical_type="storyboard",
                    logical_name=resource_id,
                    kind="last_frame",
                )
            )
        staged_media = await stage_provider_media_for_task(project_path, task_id, tuple(media_inputs))
        try:
            formal_input_claims = list(
                bind_artifact_input_claims_to_content_digests(
                    resolver=currency_resolver,
                    claims=formal_input_claims,
                    content_digests={media.source_locator: media.sha256 for media in staged_media},
                )
            )
            provider_start_image = safe_join(
                project_path,
                next(media.staged_locator for media in staged_media if media.role == "start_image"),
                require_file=True,
            )
            staged_end = next((media for media in staged_media if media.role == "end_image"), None)
            provider_end_image = (
                safe_join(project_path, staged_end.staged_locator, require_file=True)
                if staged_end is not None
                else None
            )
            visual_basis_digest = await asyncio.to_thread(
                _visual_basis_digest_for, provider_start_image, provider_end_image
            )
            artifact_visual_basis = await asyncio.to_thread(
                lambda: build_storyboard_video_artifact_visual_basis(
                    resource_id=resource_id,
                    visual_prompt=requested_visual_prompt,
                    storyboard_image=provider_start_image,
                    end_frame_image=provider_end_image,
                    aspect_ratio=aspect_ratio,
                )
            )
            artifact_video_basis = compose_video_artifact_basis(
                visual=artifact_visual_basis,
                speech=artifact_speech.basis,
                duration=artifact_duration_basis,
            )
            narration = delivery_projection.narration if delivery_projection is not None else None
            narration_facts = NarrationExecutionFacts(
                delivery=delivery_options.narration_delivery,
                tts_status=narration.tts_status.value if narration is not None else "not_applicable",
                artifact_path=narration.artifact_path if narration is not None else "",
                basis_digest=narration.basis_digest if narration is not None else None,
                actual_duration_seconds=narration.actual_duration_seconds if narration is not None else None,
            )

            async def _checkpoint_before_submit() -> Mapping[str, object]:
                await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_input_claims)
                artifact_currency = VideoArtifactCurrencyFacts(
                    episode=artifact_episode,
                    request_duration_seconds=duration_seconds,
                    visual_basis=artifact_visual_basis,
                    speech_basis=artifact_speech.basis,
                    duration_basis=artifact_duration_basis,
                    video_basis=artifact_video_basis,
                    voice_style_speakers=artifact_speech.voice_style_speakers,
                    duration_tiers=artifact_duration_tiers,
                    reference_image_limit=None,
                    parent_version=generator.versions.get_current_version("videos", resource_id),
                )
                checkpoint = StoryboardSubmissionCheckpoint.create(
                    task_id=task_id,
                    project_name=project_name,
                    script_file=script_file,
                    unit_id=resource_id,
                    generation_type="i2v",
                    provider_id=ctx.video.provider_model.provider_id,
                    provider_model_id=ctx.video.provider_model.model_id,
                    backend_model_id=ctx.video.backend_model,
                    endpoint_guard=ctx.video.endpoint,
                    prompt=prompt_text,
                    duration_seconds=duration_seconds,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    generate_audio=requested_generate_audio,
                    service_tier=service_tier,
                    seed=seed,
                    visual_basis_digest=visual_basis_digest,
                    artifact_currency=artifact_currency,
                    narration=narration_facts,
                    media=staged_media,
                    reference_audio_targets=None,
                )
                await get_generation_queue().persist_execution_checkpoint(
                    task_id,
                    checkpoint.to_json(),
                    checkpoint.provider_id,
                )
                return checkpoint_version_metadata(checkpoint)

            checkpoint_hook = _checkpoint_before_submit
        except BaseException:
            await asyncio.to_thread(cleanup_staged_provider_media, project_path, task_id)
            raise

    artifact_committer = (
        VideoArtifactCommitter(
            project_manager=get_project_manager(),
            project_name=project_name,
            project_path=project_path,
            versions=generator.versions,
            resource_type="videos",
            resource_id=resource_id,
            prompt=prompt_text,
        )
        if task_id is not None
        else None
    )
    #: backend 执行期产生的非阻断提示（如 ComfyUI 一次产出多个文件），随结果落进任务 result。
    warnings: list[dict[str, Any]] = []
    try:
        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_input_claims)
        _output_path, version, _, video_uri = await generator.generate_video_async(
            prompt=prompt_text,
            resource_type="videos",
            resource_id=resource_id,
            start_image=provider_start_image,
            end_image=provider_end_image,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            resolution=resolution,
            task_id=task_id,
            before_submit=checkpoint_hook,
            formal_output=task_id is not None,
            before_formal_commit=artifact_committer.prepare_selection if artifact_committer is not None else None,
            commit_formal_output=artifact_committer,
            seed=seed,
            service_tier=service_tier,
            visual_basis_digest=visual_basis_digest,
            generate_audio=requested_generate_audio,
            poll_timeout_seconds=poll_timeout_seconds,
            warnings=warnings,
        )

        async def _finalize() -> dict[str, Any]:
            return await finalize_video_task(
                project_name=project_name,
                script_file=script_file,
                project_path=project_path,
                resource_id=resource_id,
                version=version,
                video_uri=video_uri,
                generator=generator,
                warnings=warnings,
            )

        return await complete_video_artifact_commit(
            committer=artifact_committer,
            versions=generator.versions,
            resource_type="videos",
            resource_id=resource_id,
            version=version,
            video_uri=video_uri,
            finalize=_finalize,
            warnings=warnings,
        )
    finally:
        if artifact_committer is not None:
            await artifact_committer.release_admission_guard()
        if task_id is not None:
            await asyncio.to_thread(cleanup_staged_provider_media, project_path, task_id)


async def finalize_video_task(
    *,
    project_name: str,
    script_file: str,
    project_path: Path,
    resource_id: str,
    version: int,
    video_uri: str | None,
    generator: Any,
    warnings: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Normal + resume 共用的 finalize 逻辑：写 scene asset + 抽缩略图 + 返回 result dict。

    ``warnings`` 为空时结果不带该键：读侧按「有没有这个键」判断这一版带不带提示，恒写一个空
    列表会让每一版都多出一个永远为空的形状。
    """

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

    result: dict[str, Any] = {
        "version": version,
        "file_path": f"videos/scene_{resource_id}.mp4",
        "created_at": created_at,
        "resource_type": "videos",
        "resource_id": resource_id,
        "video_uri": video_uri,
    }
    if warnings:
        result["warnings"] = list(warnings)
    return result


async def execute_asset_sheet_task(
    kind: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """资产图（角色、场景、道具、商品）：描述取自存储的条目，参考图与依据取自生成输入。"""

    def _load() -> tuple[dict[str, Any], Path, AssetSheetInput]:
        _project = get_project_manager().load_project(project_name)
        _project_path = get_project_manager().get_project_path(project_name)
        _generation_input = asset_sheet_input(
            _project,
            asset_type=kind,
            name=resource_id,
            observation=project_input_observation(_project_path),
        )
        # 生成输入不成立时在解析供应商通道与付费之前失败，一次报出全部缺口。
        if isinstance(_generation_input, InputRefused):
            raise input_refusal_error(_generation_input)
        return _project, _project_path, _generation_input

    project, project_path, generation_input = await asyncio.to_thread(_load)
    context = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        project_path=project_path,
        user_id=user_id,
        image=ImageLaneRequest(generation_type="i2i" if generation_input.references else "t2i"),
    )

    def _freeze() -> FrozenGenerationInput:
        return generation_input.freeze(
            max_reference_images=context.image.max_reference_images,
            model=context.image.backend_model,
            bind_claims=_originals_have_no_claims,
        )

    with await asyncio.to_thread(_freeze) as frozen:
        return await run_asset_sheet_image_task(
            asset_type=kind,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
            user_id=user_id,
            task_id=task_id,
            project=project,
            frozen=frozen,
            context=context,
            project_manager=get_project_manager(),
        )


def _originals_have_no_claims(
    claims: Sequence[ArtifactInputClaim], _content_digests: Mapping[str, str]
) -> tuple[ArtifactInputClaim, ...]:
    """资产图的参考图只有作者上传的原图，原图不是登记产物，没有 claim 要复核。"""

    if claims:
        raise ValueError("asset sheet references carry no artifact input claims")
    return ()


async def execute_character_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_asset_sheet_task(
        "character", project_name, resource_id, payload, user_id=user_id, task_id=task_id
    )


async def execute_scene_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_asset_sheet_task("scene", project_name, resource_id, payload, user_id=user_id, task_id=task_id)


async def execute_prop_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_asset_sheet_task("prop", project_name, resource_id, payload, user_id=user_id, task_id=task_id)


async def execute_product_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_asset_sheet_task(
        "product", project_name, resource_id, payload, user_id=user_id, task_id=task_id
    )


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
    1. Load GridGeneration (an already completed record ends the task without generating),
       set status to generating
    2. Generate the joint image via MediaGenerator (versioned as resource_type "grids")
    3. Mark completed; splitting into storyboard cells is a separate, explicit step
    """
    from lib.script.grid.grid_manager import GridManager
    from lib.script.grid.layout import GRID_FALLBACK_RESOLUTION, grid_aspect_ratio_for
    from lib.script.grid.prompt_builder import build_grid_prompt

    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    grid_manager = GridManager(project_path)

    # a) Load grid
    grid = grid_manager.get(resource_id)
    if grid is None:
        raise ValueError(f"grid not found: {resource_id}")
    project = await asyncio.to_thread(get_project_manager().load_project, project_name)
    if grid.status == "completed":
        # 新建与重生成的记录都从 pending 起步，失败重试从 failed 起步：记录已是 completed，只能是
        # 沿用在途宫格时恰好赶上上一任务完成而重复入队的任务，不再出图、不再计费。
        # 结果只在联合图登记在案且可用时报成功，与切分落格同一口径。
        grid_path = f"grids/{resource_id}.png"
        resolver = await asyncio.to_thread(active_artifact_currency_resolver, project_path, project)
        comparison = await asyncio.to_thread(
            resolver.compare, ArtifactKey.episode_grid(grid.episode, resource_id), artifact_path=grid_path
        )
        if not comparison.usable:
            raise ValueError(f"grid {resource_id} is completed but its composite is not usable")
        logger.info("宫格已完成，跳过重复入队的生成任务: grid_id=%s task_id=%s", resource_id, task_id)
        return {"file_path": grid_path, "resource_type": "grids", "resource_id": resource_id}
    script = await asyncio.to_thread(get_project_manager().load_script, project_name, grid.script_file)
    script_input = await asyncio.to_thread(
        resolve_usable_episode_script_input,
        project_path=project_path,
        project=project,
        script=script,
        script_filename=grid.script_file,
    )
    artifact_episode = script_input.episode
    if artifact_episode != grid.episode:
        raise ValueError(f"grid episode {grid.episode} does not match bound script episode {artifact_episode}")

    frozen_references: FrozenImageReferences | None = None
    try:
        # b) Set status to generating
        grid.status = "generating"
        grid.error_message = None
        grid_manager.save(grid)

        items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
        item_by_id = {str(item.get(id_field)): item for item in items if isinstance(item, dict)}
        if len(set(grid.scene_ids)) != len(grid.scene_ids):
            raise ValueError("grid scene identities must be unique")
        missing_members = [scene_id for scene_id in grid.scene_ids if scene_id not in item_by_id]
        if missing_members:
            raise ValueError(f"grid scenes are no longer present in the bound script: {missing_members}")

        # c) Build reference images + metadata
        from lib.script.grid.models import ReferenceImage

        grid_input = await asyncio.to_thread(
            grid_references,
            project,
            script,
            member_ids=grid.scene_ids,
            observation=project_input_observation(project_path),
        )
        # 参考图集不成立时在解析供应商通道与付费之前失败，一次报出全部缺口。
        if isinstance(grid_input, InputRefused):
            raise input_refusal_error(grid_input)
        assembled = grid_input.references
        visual_references = [reference.visual for reference in assembled]
        currency_resolver = await asyncio.to_thread(active_artifact_currency_resolver, project_path, project)
        # 参考图先于提示词定型：backend 的上限决定实际发出几张，各格正文的「图N」只能按
        # 裁剪后的序列渲染，故 image lane 在冻结之前解析一次并沿用到提交。
        ctx = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            project_path=project_path,
            user_id=user_id,
            image=ImageLaneRequest(generation_type="i2i" if assembled else "t2i"),
        )
        frozen_references = await asyncio.to_thread(
            freeze_image_references,
            [reference.visual.path for reference in assembled] or None,
            visual_references,
        )
        # 剧本 claim 排在最前，其后是参考图按冻结字节绑定的 claims。
        formal_claims = list(
            await asyncio.to_thread(
                bind_artifact_input_claims_to_frozen_visuals,
                project_path=project_path,
                resolver=currency_resolver,
                claims=[
                    script_input.claim,
                    *(reference.claim for reference in assembled if reference.claim is not None),
                ],
                source_references=visual_references,
                frozen_references=frozen_references.visual_references,
            )
        )
        # 依据与宫格记录按完整装配集登记，供应商只收裁剪后的前几张（与分镜路径同口径）；
        # 裁剪 warning 随任务结果返回。
        reference_clamp = clamp_reference_images(
            visual_references, ctx.image.max_reference_images, model=ctx.image.backend_model
        )
        sent_references = frozen_references.sent(reference_clamp.kept)
        reference_images = sent_references.reference_images
        # 宫格的时效判定重放这份记录，故由完整参考集投影，与依据里的参考图逐项对应。
        grid.reference_images = []
        for reference in assembled:
            visual = reference.visual
            if visual.logical_type is None or visual.logical_id is None:
                raise ValueError(f"grid reference has no asset identity: {reference.artifact_path}")
            grid.reference_images.append(
                ReferenceImage(path=reference.artifact_path, name=visual.logical_id, ref_type=visual.logical_type)
            )
        grid_manager.save(grid)

        # d) Generate grid image
        members = tuple(
            GridStoryboardVisual(
                resource_id=scene_id,
                image_prompt=item_by_id[scene_id].get("image_prompt"),
                video_prompt=item_by_id[scene_id].get("video_prompt"),
            )
            for scene_id in grid.scene_ids
        )
        member_aspect_ratio = grid.video_aspect_ratio or get_aspect_ratio(project, "storyboards")
        grid_aspect_ratio = grid_aspect_ratio_for(grid.rows, grid.cols, member_aspect_ratio)
        prompt_text = build_grid_prompt(
            scenes=[item_by_id[scene_id] for scene_id in grid.scene_ids],
            id_field=id_field,
            rows=grid.rows,
            cols=grid.cols,
            style=normalize_style_value(project.get("style")),
            style_description=normalize_style_value(project.get("style_description")),
            aspect_ratio=member_aspect_ratio,
            grid_aspect_ratio=grid_aspect_ratio,
            references=sent_references.visual_references,
        )
        grid_basis = build_grid_composite_visual_basis(
            group_id=grid.id,
            members=members,
            rows=grid.rows,
            columns=grid.cols,
            style=str(project.get("style") or ""),
            style_description=project_basis_style_description(project),
            grid_aspect_ratio=grid_aspect_ratio,
            references=frozen_references.visual_references,
        )
        generator = ctx.generator
        aspect_ratio = grid_aspect_ratio

        # 回填 grid metadata：route 层创建/重建时无法预知 needs_i2i，由此处补齐。
        # provider 记 registry 身份（供后续重解析定位供应商），model 记 backend 实际身份
        # （自定义供应商目标 model 被禁用回退时，实际调用的 model 与解析出的 model_id 不同）。
        grid.provider = ctx.image.provider_model.provider_id
        grid.model = ctx.image.backend_model
        grid.prompt = prompt_text
        grid_manager.save(grid)
        # 保底档与档位门控（``large_grid_allowed``）取同一常量，避免门控按 2K 判定、
        # 渲染却按别的档位下发
        image_size = ctx.image.resolution or GRID_FALLBACK_RESOLUTION
        formal_outcomes: list[FormalImageCommitOutcome] = []

        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

        async def _before_submit() -> None:
            await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

        await generator.generate_image_async(
            prompt=prompt_text,
            resource_type="grids",
            resource_id=resource_id,
            reference_images=reference_images,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            before_submit=_before_submit,
            formal_output=True,
            task_id=task_id,
            commit_formal_output=grid_formal_image_callback(
                project_path=project_path,
                grid_manager=grid_manager,
                grid=grid,
                resource_id=resource_id,
                prompt=str(prompt_text),
                versions=generator.versions,
                task_id=task_id,
                basis=grid_basis,
                outcome_box=formal_outcomes,
            ),
        )

        # formal_output=True 时 generate_image_async 恒经活化回调提交，回调恰好记录一条结果。
        outcome = formal_outcomes[0]

    except Exception:
        # The formal-write transaction restores the durable grid record on failure.
        # Reload it before recording failure so the in-memory ``grid`` never carries
        # completion fields that did not become durable.
        grid = grid_manager.get(resource_id) or grid
        grid.status = "failed"
        import traceback

        grid.error_message = traceback.format_exc()
        grid_manager.save(grid)
        raise
    finally:
        if frozen_references is not None:
            await run_noninterruptible_sync(frozen_references.cleanup)

    grid_result: dict[str, Any] = {
        "version": outcome.version,
        "file_path": f"grids/{resource_id}.png",
        "created_at": outcome.created_at,
        "resource_type": "grids",
        "resource_id": resource_id,
    }
    if (clamp_warning := reference_clamp.warning()) is not None:
        grid_result["warnings"] = [clamp_warning]
    return grid_result


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
    "reference_video": execute_reference_video_task,
    "image_edit": execute_image_edit_task,
    DERIVATIVE_TASK_TYPE: execute_character_derivative_task,
}


async def execute_generation_task(task: dict[str, Any], *, claimed_provider_id: str | None = None) -> dict[str, Any]:
    task_type = task.get("task_type")
    project_name = task.get("project_name")
    resource_id = str(task.get("resource_id"))
    payload = task.get("payload") or {}
    user_id = task.get("user_id", DEFAULT_USER_ID)
    queue_task_id = task.get("task_id")
    # worker 派发时把当下的全局设置写进任务字典；非 worker 调用方（测试 / 直生）落回缺省。
    video_poll_timeout_seconds = int(task.get("video_poll_timeout_seconds", DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS))

    if not project_name:
        raise ValueError("task.project_name is required")
    if not task_type:
        raise ValueError("task.task_type is required")

    executor = _TASK_EXECUTORS.get(task_type)
    if task_type.startswith("text_"):
        from server.tool_runtime import execute_queued_text_task

        return await execute_queued_text_task(task)
    if executor is None:
        raise ValueError(f"unsupported task_type: {task_type}")

    with project_change_source("worker"):
        # 能力类异常（Image/VideoCapabilityError、ReferencePayloadFloorError）原样上抛：
        # worker 的 encode_task_failure_message 按 code + params 落库，渲染留到读侧
        # Translator，同一失败任务按 Accept-Language 显示 zh/en/vi。
        if task_type in ("video", "reference_video"):
            result = await executor(
                project_name,
                resource_id,
                payload,
                script_file=task.get("script_file"),
                user_id=user_id,
                task_id=queue_task_id,
                claimed_provider_id=claimed_provider_id,
                poll_timeout_seconds=video_poll_timeout_seconds,
            )
        else:
            result = await executor(project_name, resource_id, payload, user_id=user_id, task_id=queue_task_id)
        # 成功事件发出失败时不回滚产物：已写入的产物保持有效，异常照常上抛。
        emit_generation_success_batch(
            task_type=task_type,
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
        )
        return result
