"""
Task execution service for queued generation jobs.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from lib.api_errors import ConflictError
from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    ArtifactInputClaim,
    ArtifactRegistrationReceipt,
    active_artifact_currency_resolver,
    artifact_input_is_usable,
    assert_current_artifact_input_claims_usable,
    bind_artifact_input_claims_to_content_digests,
    bind_artifact_input_claims_to_frozen_visuals,
    register_current_resource_artifact,
    register_task_current_resource_artifact,
    resolve_current_resource_artifact_basis,
    resolve_usable_episode_script_input,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.asset_types import (
    ASSET_SPECS,
    AssetSpec,
    normalize_asset_bucket,
    normalize_asset_name,
    resolve_asset_key,
    validate_asset_name,
)
from lib.async_thread import EventLoopBridge, run_noninterruptible_sync
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
from lib.generation_queue import (
    CompensableGenerationResult,
    DispatchProviderChanged,
    get_generation_queue,
    without_video_execution_identity,
)
from lib.image_reference_snapshot import FrozenImageReferences, freeze_image_references
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
from lib.path_safety import safe_exists, safe_join, try_safe_join
from lib.project_change_hints import build_change_label, emit_project_change_batch, project_change_source
from lib.project_manager import (
    EpisodeScriptReboundError,
    ProjectManager,
    get_project_manager,
    is_reference_video_project,
    resolve_episode_script_binding,
)
from lib.prompt_builders import (
    append_product_fidelity_tail,
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
    build_storyboard_prompt,
)
from lib.prompt_utils import (
    build_drama_video_prompt,
    build_drama_video_prompt_from_legacy_dialogue,
    strip_voice_profiles,
)
from lib.prompt_utils import (
    normalize_video_prompt as _normalize_video_prompt,
)
from lib.reference_video.execution_checkpoint import (
    NarrationExecutionFacts,
    ProviderMediaInput,
    StagedProviderMedia,
    StoryboardSubmissionCheckpoint,
    TextVideoSubmissionCheckpoint,
    checkpoint_version_metadata,
    cleanup_staged_provider_media,
    stage_provider_media_for_task,
)
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.script_models import resolve_content_mode
from lib.script_skeleton import SKELETON_ENTITY_TYPES, SKELETON_ITEM_LABEL_KEYS, resolve_script_kind
from lib.speech_artifact_provenance import build_video_duration_basis
from lib.speech_composition import SpeechAdmissionError, admit_script_unit
from lib.storyboard_sequence import (
    build_previous_storyboard_reference,
    find_storyboard_item,
    get_storyboard_items,
    group_scenes_by_segment_break,
    resolve_previous_storyboard_path,
)
from lib.thumbnail import extract_video_thumbnail
from lib.version_manager import PaidVersionCommit
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.video_backends.base import VideoCapabilityError
from lib.video_output_normalization import VideoOutputNormalizationPolicy
from lib.video_visual_provenance import (
    build_storyboard_video_visual_basis,
    build_text_video_visual_basis,
    resolve_video_aspect_ratio,
)
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_asset_sheet_visual_basis,
    build_grid_composite_visual_basis,
    build_storyboard_image_visual_basis,
    build_storyboard_video_artifact_visual_basis,
    build_text_video_artifact_visual_basis,
)
from server.services.generation_context import (
    AudioLaneRequest,
    ImageLaneRequest,
    VideoLaneRequest,
    resolve_generation_context,
)
from server.services.image_artifact_currency import (
    OptimisticMappingMemberPatch,
    OptimisticMappingPatch,
    SelectedImageArtifactReceipt,
    reject_failed_image_selection,
)
from server.services.narration_delivery_tasks import (
    CurrentTtsSettingsResolver,
    ResolvedTtsSettingsResolver,
    active_narrated_video_resource_ids,
    current_selected_video_tier,
    reuse_current_video_for_tier,
    tts_task_in_progress,
)
from server.services.video_artifact_currency import (
    VideoArtifactCommitter,
    complete_video_artifact_commit,
    freeze_video_speech_facts,
)

logger = logging.getLogger(__name__)


class _CancellationReceipt(Protocol):
    def compensate_cancelled(self) -> None:
        pass


def register_formal_task_artifact(
    project_path: Path,
    *,
    resource_type: str,
    resource_id: str,
    script_file: str | None,
    task_id: str | None,
    artifact_path: str | None = None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
) -> ArtifactRegistrationReceipt | None:
    """Use the task-aware registration seam only when a terminal gate exists."""

    if task_id is None:
        register_current_resource_artifact(
            project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            script_file=script_file,
            artifact_path=artifact_path,
            basis=basis,
        )
        return None
    return register_task_current_resource_artifact(
        project_path,
        resource_type=resource_type,
        resource_id=resource_id,
        script_file=script_file,
        artifact_path=artifact_path,
        basis=basis,
    )


async def run_formal_task_finalizer[T](
    finalize: Callable[[], T],
    *,
    task_id: str | None,
    compensate_failure: Callable[[], None] | None = None,
) -> T:
    """Finish a task's formal-write transaction so cancellation can compensate it."""

    def _finalize_with_compensation() -> T:
        try:
            return finalize()
        except BaseException as failure:
            if compensate_failure is not None:
                try:
                    compensate_failure()
                except BaseException as compensation_failure:
                    failure.add_note(f"formal image selection compensation also failed: {compensation_failure}")
            raise

    # A synchronous formal-write thread cannot be stopped after cancellation.
    # Always await its durable outcome before the caller leaves this boundary.
    return await run_noninterruptible_sync(_finalize_with_compensation)


def compensable_formal_task_result(
    result: dict[str, Any],
    receipt: _CancellationReceipt | None,
) -> dict[str, Any]:
    if receipt is None:
        return result
    return CompensableGenerationResult(result, cancel_compensation=receipt.compensate_cancelled)


def get_aspect_ratio(project: dict, resource_type: str) -> str:
    if resource_type in ("characters", "scenes", "props", "products"):
        # 资产图生成必须显式指定宽高比；四类当前均固定为 16:9。
        return "16:9"
    return resolve_video_aspect_ratio(project, resource_type)


@dataclass(frozen=True, slots=True)
class _FormalImageCommitOutcome:
    """Durable result produced inside the shared image activation seam."""

    version: int
    created_at: str
    receipt: _CancellationReceipt | None


# 正式图提交三件套的公共签名：活化回调 / 元数据补偿器 / 元数据提交器。
type _StagedImageCommit = Callable[[Path, Path, Mapping[str, Any]], int]
type _MetadataCompensator = Callable[[Callable[[], None]], None]
type _MetadataCommitter = Callable[[Callable[[], None]], _MetadataCompensator | None]


def _formal_image_task_token(task_id: str | None) -> str:
    """Give direct invocations an isolated staging identity without inventing a queue identity."""

    return task_id or f"inline-{uuid.uuid4().hex}"


def _created_at_for_version(versions: Any, resource_type: str, resource_id: str, version: int) -> str:
    records = versions.get_versions(resource_type, resource_id).get("versions", [])
    for record in records:
        if record.get("version") == version:
            created_at = record.get("created_at")
            if isinstance(created_at, str) and created_at:
                return created_at
    raise RuntimeError("formal image version metadata is missing its creation timestamp")


def _commit_staged_formal_image(
    *,
    versions: Any,
    project_path: Path,
    resource_type: str,
    resource_id: str,
    script_file: str | None,
    artifact_path: str,
    prompt: str,
    staged_file: Path,
    current_file: Path,
    version_metadata: Mapping[str, Any],
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    commit_metadata: _MetadataCommitter,
) -> _FormalImageCommitOutcome:
    """Commit metadata → selected bytes/version → Manifest through one nested transaction.

    Every formal image entry point supplies only its metadata mutation.  Version
    activation and registration stay centralized so no caller can publish a
    canonical file before all dependent state is ready to commit.
    """

    manifest_box: list[ArtifactRegistrationReceipt | None] = []
    version_box: list[int] = []
    created_at_box: list[str] = []
    registered_version_box: list[int] = []
    resolved_basis_box: list[ArtifactBasis | ArtifactBasisDescriptor | None] = []

    def _register() -> None:
        registered_version = versions.get_current_version(resource_type, resource_id)
        if type(registered_version) is not int or registered_version < 1:
            raise RuntimeError("formal image staged activation has no selected version")
        registered_version_box.append(registered_version)
        created_at_box.append(_created_at_for_version(versions, resource_type, resource_id, registered_version))
        manifest_box.append(
            register_formal_task_artifact(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
                script_file=script_file,
                task_id=task_id,
                artifact_path=artifact_path,
                basis=resolved_basis_box[0],
            )
        )

    def _activate() -> None:
        resolved_basis = basis
        if resolved_basis is None:
            resolved_basis = resolve_current_resource_artifact_basis(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
                script_file=script_file,
            )
        resolved_basis_box.append(resolved_basis)
        committed_metadata = dict(version_metadata)
        if IMAGE_ARTIFACT_BASIS_FIELD in committed_metadata:
            raise ValueError(f"{IMAGE_ARTIFACT_BASIS_FIELD} is reserved for formal image activation")
        if isinstance(resolved_basis, ArtifactBasis):
            committed_metadata[IMAGE_ARTIFACT_BASIS_FIELD] = resolved_basis.to_evidence_dict()
        version_box.append(
            versions.commit_staged_version(
                resource_type=resource_type,
                resource_id=resource_id,
                prompt=prompt,
                staged_file=staged_file,
                current_file=current_file,
                on_commit=_register,
                **committed_metadata,
            )
        )

    compensate_metadata = commit_metadata(_activate)
    if (
        len(version_box) != 1
        or len(registered_version_box) != 1
        or version_box != registered_version_box
        or len(created_at_box) != 1
        or len(manifest_box) != 1
        or len(resolved_basis_box) != 1
    ):
        raise RuntimeError("formal image metadata commit skipped staged activation")
    version = version_box[0]
    manifest = manifest_box[0]
    receipt: _CancellationReceipt | None = None
    if task_id is not None:
        if manifest is None or compensate_metadata is None:
            raise RuntimeError("task-aware formal image commit did not return compensation state")
        receipt = SelectedImageArtifactReceipt(
            versions=versions,
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            current_file=current_file,
            manifest=manifest,
            compensate_metadata=compensate_metadata,
        )
    return _FormalImageCommitOutcome(
        version=version,
        created_at=created_at_box[0],
        receipt=receipt,
    )


def _normalize_storyboard_prompt(prompt: object, style: str, style_description: str = "") -> str:
    """Render one semantic storyboard prompt through the shared provider projection."""

    return build_storyboard_prompt(prompt, style, style_description)


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
    visual_references: list[VisualReference] | None = None,
    currency_resolver: ArtifactCurrencyResolver,
    formal_claims: list[ArtifactInputClaim] | None = None,
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

    sources = (
        (
            "character",
            char_field,
            normalize_asset_bucket(project.get("characters")),
            "character_sheet",
        ),
        (
            "scene",
            scene_field,
            normalize_asset_bucket(project.get("scenes")),
            "scene_sheet",
        ),
        (
            "prop",
            prop_field,
            normalize_asset_bucket(project.get("props")),
            "prop_sheet",
        ),
    )

    for item in items:
        for asset_type, field, bucket, sheet_field in sources:
            for name in item.get(field) or []:
                if not isinstance(name, str):
                    continue
                canonical_name = normalize_asset_name(name)
                data = bucket.get(canonical_name)
                sheet = data.get(sheet_field) if isinstance(data, dict) else None
                if not isinstance(sheet, str) or not sheet or sheet in seen:
                    continue
                path = project_path / sheet
                if not path.exists():
                    continue
                if max_count and len(refs) >= max_count:
                    seen.add(sheet)
                    continue
                key = ArtifactKey.asset_sheet(asset_type, canonical_name)
                if not artifact_input_is_usable(
                    resolver=currency_resolver,
                    key=key,
                    artifact_path=sheet,
                    claims=formal_claims,
                ):
                    continue
                refs.append({"image": path, "label": name})
                if visual_references is not None:
                    visual_references.append(
                        VisualReference(
                            path=path,
                            role="asset_sheet",
                            logical_type=asset_type,
                            logical_id=name,
                            kind="sheet",
                        )
                    )
                seen.add(sheet)
        if max_count and len(refs) >= max_count:
            break

    return refs, seen


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
    previous_storyboard_id: str | None = None,
    visual_references: list[VisualReference] | None = None,
    artifact_episode: int | None = None,
    currency_resolver: ArtifactCurrencyResolver,
    formal_claims: list[ArtifactInputClaim] | None = None,
) -> list[object] | None:
    sheet_refs, _ = _collect_sheet_references(
        project,
        project_path,
        [target_item],
        char_field=char_field,
        scene_field=scene_field,
        prop_field=prop_field,
        visual_references=visual_references,
        currency_resolver=currency_resolver,
        formal_claims=formal_claims,
    )
    reference_images: list[object] = list(sheet_refs)

    for extra in extra_reference_images or []:
        extra_path = Path(extra)
        if not extra_path.is_absolute():
            extra_path = project_path / extra_path
        if extra_path.exists():
            reference_images.append(extra_path)
            if visual_references is not None:
                visual_references.append(VisualReference(path=extra_path, role="extra_reference"))

    if previous_storyboard_path and previous_storyboard_path.exists():
        if not previous_storyboard_id:
            raise ValueError("previous_storyboard_id is required for storyboard basis evidence")
        if artifact_episode is None:
            raise ValueError("artifact_episode is required for storyboard references")
        previous_artifact_path = previous_storyboard_path.relative_to(project_path).as_posix()
        previous_key = ArtifactKey.episode_storyboard(artifact_episode, previous_storyboard_id)
        if not artifact_input_is_usable(
            resolver=currency_resolver,
            key=previous_key,
            artifact_path=previous_artifact_path,
            claims=formal_claims,
        ):
            return reference_images or None
        reference_images.append(build_previous_storyboard_reference(previous_storyboard_path))
        if visual_references is not None:
            visual_references.append(
                VisualReference(
                    path=previous_storyboard_path,
                    role="previous_storyboard",
                    logical_type="storyboard",
                    logical_id=previous_storyboard_id,
                )
            )

    return reference_images or None


def _collect_shot_product_references(
    project: dict,
    project_path: Path,
    item: dict,
    *,
    currency_resolver: ArtifactCurrencyResolver,
    formal_claims: list[ArtifactInputClaim] | None = None,
) -> list[dict]:
    """商品分镜（``products_in_shot`` 非空）的商品参考集，用于分镜图生成。

    每个商品：有 product sheet 时注入集为「sheet 多角度 + 原图压阵」（sheet 在前、
    原图收尾），无 sheet 时原图直注。返回 ``{"image": Path, "label": str, "name": str,
    "kind": "sheet"|"original"}`` 列表——label 供支持内联标签的后端绑定图与商品名，
    name 供高保真指令点名（指令只点名实际注入了参考的商品），kind 供截断时让 sheet
    优先存活；调用方负责把该列表排在其它参考之前（排序绝对优先）。氛围分镜
    （列表为空）返回空列表，零商品图。脏数据（products_in_shot 非列表、products
    非 dict、商品名非字符串、引用不存在的商品）按既有装配口径跳过不抛。
    """
    raw_products_in_shot = item.get("products_in_shot")
    if not isinstance(raw_products_in_shot, (list, tuple)):
        if raw_products_in_shot:
            logger.warning(
                "products_in_shot 类型异常（%s），商品参考注入跳过",
                type(raw_products_in_shot).__name__,
            )
        return []
    return collect_product_references_for_names(
        project,
        project_path,
        raw_products_in_shot,
        currency_resolver=currency_resolver,
        formal_claims=formal_claims,
    )


def collect_product_references_for_names(
    project: dict,
    project_path: Path,
    names: Sequence[str],
    *,
    currency_resolver: ArtifactCurrencyResolver,
    formal_claims: list[ArtifactInputClaim] | None = None,
) -> list[dict]:
    """按商品名列表收集商品参考集（注入二元规则的装配核心，条目语义见
    ``_collect_shot_product_references``）。分镜图按分镜注入与广告/短片的参考生视频
    按 unit 注入共用此函数，保证两条路径的「sheet 在前、原图压阵」口径一致。
    """
    spec = ASSET_SPECS["product"]
    products = normalize_asset_bucket(project.get(spec.bucket_key))
    references: list[dict] = []
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            logger.warning("products_in_shot 含非字符串条目 %r，商品参考跳过", name)
            continue
        canonical = normalize_asset_name(name)
        if canonical in seen:
            continue
        seen.add(canonical)
        entry = products.get(canonical)
        if not isinstance(entry, dict):
            logger.warning("分镜引用的商品 '%s' 不在 project.json products 中，商品参考跳过", name)
            continue
        before = len(references)
        sheet = entry.get(spec.sheet_field)
        if (
            isinstance(sheet, str)
            and safe_exists(project_path, sheet)
            and artifact_input_is_usable(
                resolver=currency_resolver,
                key=ArtifactKey.asset_sheet("product", canonical),
                artifact_path=sheet,
                claims=formal_claims,
            )
        ):
            references.append(
                {
                    "image": project_path / sheet,
                    "label": f"商品「{canonical}」标准多角度参考图",
                    "name": canonical,
                    "kind": "sheet",
                }
            )
        for original in _collect_product_reference_images(project, project_path, canonical) or []:
            references.append(
                {
                    "image": original,
                    "label": f"商品「{canonical}」实拍原图（保真锚点）",
                    "name": canonical,
                    "kind": "original",
                }
            )
        if len(references) == before:
            logger.warning("商品分镜引用的商品 '%s' 无任何可用参考图（sheet 与原图均缺失），保真注入退化为纯文本", name)
    return references


def _product_names_in_references(product_references: list[dict]) -> list[str]:
    """从商品参考集提取去重保序的商品名——高保真指令只点名实际注入了参考的商品。"""
    return list(dict.fromkeys(ref["name"] for ref in product_references))


def _product_visual_references(product_references: Sequence[Mapping[str, object]]) -> list[VisualReference]:
    """Project provider-facing product refs into canonical generation evidence."""

    evidence: list[VisualReference] = []
    for reference in product_references:
        path = reference.get("image")
        name = reference.get("name")
        kind = reference.get("kind")
        if not isinstance(path, Path) or not isinstance(name, str) or kind not in {"sheet", "original"}:
            raise ValueError("product reference metadata is incomplete")
        evidence.append(
            VisualReference(
                path=path,
                role="asset_sheet" if kind == "sheet" else "source",
                logical_type="product",
                logical_id=name,
                kind=str(kind),
            )
        )
    return evidence


def _staged_formal_image_callback(
    *,
    versions: Any,
    project_path: Path,
    resource_type: str,
    resource_id: str,
    script_file: str | None,
    artifact_path: str,
    prompt: str,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    outcome_box: list[_FormalImageCommitOutcome],
    commit_metadata: _MetadataCommitter,
) -> _StagedImageCommit:
    """Wrap one metadata committer into the staged activation callback of an image task."""

    def _commit(staged_file: Path, current_file: Path, version_metadata: Mapping[str, Any]) -> int:
        outcome = _commit_staged_formal_image(
            versions=versions,
            project_path=project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            script_file=script_file,
            artifact_path=artifact_path,
            prompt=prompt,
            staged_file=staged_file,
            current_file=current_file,
            version_metadata=version_metadata,
            task_id=task_id,
            basis=basis,
            commit_metadata=commit_metadata,
        )
        outcome_box.append(outcome)
        return outcome.version

    return _commit


def _asset_sheet_metadata_mutator(
    *,
    spec: AssetSpec,
    resource_id: str,
    sheet_path: str,
    mutation_box: list[OptimisticMappingPatch],
) -> Callable[[dict[str, Any]], None]:
    """Point one asset entry at its new sheet and record the patch for compensation."""

    def _mutate(project: dict[str, Any]) -> None:
        bucket = project.get(spec.bucket_key)
        key = resolve_asset_key(bucket, resource_id)
        if not isinstance(bucket, dict) or key is None:
            raise KeyError(f"{spec.label_zh} '{resource_id}' 不存在")
        entry = bucket[key]
        if not isinstance(entry, dict):
            raise ValueError(f"{spec.label_zh} '{resource_id}' metadata must be an object")
        before = copy.deepcopy(entry)
        entry[spec.sheet_field] = sheet_path
        mutation_box.append(OptimisticMappingPatch.capture(before, entry))

    return _mutate


def _asset_sheet_metadata_compensator(
    *,
    pm: ProjectManager,
    project_name: str,
    spec: AssetSpec,
    resource_id: str,
    mutation: OptimisticMappingPatch,
) -> _MetadataCompensator:
    """Roll the asset entry back to the pre-write patch inside the rejecting transaction."""

    def _compensate_metadata(reject: Callable[[], None]) -> None:
        def _restore(project: dict[str, Any]) -> None:
            bucket = project.get(spec.bucket_key)
            key = resolve_asset_key(bucket, resource_id)
            if isinstance(bucket, dict) and key is not None and isinstance(bucket[key], dict):
                mutation.restore(bucket[key])

        def _reject(_project_file: Path) -> None:
            reject()

        pm.update_project(project_name, _restore, on_commit=_reject)

    return _compensate_metadata


def _write_storyboard_image_metadata(
    *,
    pm: ProjectManager,
    project_name: str,
    script_file: str,
    resource_id: str,
    artifact_path: str,
    mutation_box: list[OptimisticMappingMemberPatch],
    on_commit: Callable[[Path], None],
) -> None:
    """Point one storyboard item at its new image and record the patch for compensation."""

    with pm.locked_script(project_name, script_file, validate=False, on_commit=on_commit) as script:
        items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, resource_id)
        if resolved is None:
            raise KeyError(f"场景 '{resource_id}' 不存在")
        item, _index = resolved
        before = copy.deepcopy(item)
        pm._set_scene_asset_in_script(script, resource_id, "storyboard_image", artifact_path)
        selected_assets = item.get("generated_assets")
        if not isinstance(selected_assets, Mapping):
            raise RuntimeError("storyboard metadata commit did not produce generated_assets")
        mutation_box.append(OptimisticMappingMemberPatch.capture(before, "generated_assets", selected_assets))


def _storyboard_metadata_compensator(
    *,
    pm: ProjectManager,
    project_name: str,
    script_file: str,
    resource_id: str,
    mutation: OptimisticMappingMemberPatch,
) -> _MetadataCompensator:
    """Roll the storyboard item back; a vanished script or item still has to reject the version."""

    def _compensate_metadata(reject: Callable[[], None]) -> None:
        def _reject(_script_path: Path) -> None:
            reject()

        try:
            with pm.locked_script(
                project_name,
                script_file,
                validate=False,
                on_commit=_reject,
            ) as script:
                items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
                resolved = find_storyboard_item(items, id_field, resource_id)
                if resolved is not None:
                    mutation.restore(resolved[0])
        except (FileNotFoundError, KeyError):
            reject()

    return _compensate_metadata


def _asset_sheet_formal_image_callback(
    *,
    asset_type: str,
    project_name: str,
    resource_id: str,
    sheet_path: str,
    prompt: str,
    versions: Any,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    outcome_box: list[_FormalImageCommitOutcome],
    project_manager: ProjectManager | None = None,
) -> _StagedImageCommit:
    """Build the shared staged activation callback for every asset-sheet task."""

    spec = ASSET_SPECS[asset_type]
    pm = project_manager or get_project_manager()
    project_path = pm.get_project_path(project_name)

    def _commit_metadata(activate: Callable[[], None]) -> _MetadataCompensator | None:
        mutation_box: list[OptimisticMappingPatch] = []

        def _activate(_project_file: Path) -> None:
            activate()

        pm.update_project(
            project_name,
            _asset_sheet_metadata_mutator(
                spec=spec,
                resource_id=resource_id,
                sheet_path=sheet_path,
                mutation_box=mutation_box,
            ),
            on_commit=_activate,
        )
        if task_id is None:
            return None
        return _asset_sheet_metadata_compensator(
            pm=pm,
            project_name=project_name,
            spec=spec,
            resource_id=resource_id,
            mutation=mutation_box[0],
        )

    return _staged_formal_image_callback(
        versions=versions,
        project_path=project_path,
        resource_type=spec.bucket_key,
        resource_id=resource_id,
        script_file=None,
        artifact_path=sheet_path,
        prompt=prompt,
        task_id=task_id,
        basis=basis,
        outcome_box=outcome_box,
        commit_metadata=_commit_metadata,
    )


async def _finalize_formal_image_task(
    *,
    project_path: Path,
    resource_type: str,
    resource_id: str,
    script_file: str | None,
    artifact_path: str,
    generator: Any,
    version: int,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    commit_current: Callable[[Callable[[Path], None]], None],
    commit_tracked: Callable[[Callable[[Path], None]], _MetadataCompensator],
    missing_receipt_error: str,
) -> tuple[str, _CancellationReceipt | None]:
    """Commit one image's metadata plus selection and span the task terminal-cancellation window."""

    def _finalize() -> tuple[str, _CancellationReceipt | None]:
        created_at = generator.versions.get_versions(resource_type, resource_id)["versions"][-1]["created_at"]
        if task_id is None:

            def _register_current(_committed_file: Path) -> None:
                register_formal_task_artifact(
                    project_path,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    script_file=script_file,
                    task_id=None,
                    artifact_path=artifact_path,
                    basis=basis,
                )

            commit_current(_register_current)
            return created_at, None

        manifest_box: list[ArtifactRegistrationReceipt] = []

        def _register(_committed_file: Path) -> None:
            receipt = register_formal_task_artifact(
                project_path,
                resource_type=resource_type,
                resource_id=resource_id,
                script_file=script_file,
                task_id=task_id,
                artifact_path=artifact_path,
                basis=basis,
            )
            if receipt is None:
                raise RuntimeError(missing_receipt_error)
            manifest_box.append(receipt)

        compensate_metadata = commit_tracked(_register)
        receipt = SelectedImageArtifactReceipt(
            versions=generator.versions,
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            current_file=project_path / artifact_path,
            manifest=manifest_box[0],
            compensate_metadata=compensate_metadata,
        )
        return created_at, receipt

    def _compensate_failed_selection() -> None:
        reject_failed_image_selection(
            versions=generator.versions,
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
            current_file=project_path / artifact_path,
        )

    return await run_formal_task_finalizer(
        _finalize,
        task_id=task_id,
        compensate_failure=_compensate_failed_selection,
    )


async def _finalize_asset_sheet_task(
    *,
    asset_type: str,
    project_name: str,
    resource_id: str,
    sheet_path: str,
    generator: Any,
    version: int,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
    project_manager: ProjectManager | None = None,
) -> tuple[str, _CancellationReceipt | None]:
    """Commit one asset sheet and span the task terminal-cancellation window."""

    spec = ASSET_SPECS[asset_type]
    pm = project_manager or get_project_manager()

    def _commit_current(register: Callable[[Path], None]) -> None:
        pm._update_asset_sheet(
            asset_type,
            project_name,
            resource_id,
            sheet_path,
            on_commit=register,
        )

    def _commit_tracked(register: Callable[[Path], None]) -> _MetadataCompensator:
        mutation_box: list[OptimisticMappingPatch] = []
        pm.update_project(
            project_name,
            _asset_sheet_metadata_mutator(
                spec=spec,
                resource_id=resource_id,
                sheet_path=sheet_path,
                mutation_box=mutation_box,
            ),
            on_commit=register,
        )
        return _asset_sheet_metadata_compensator(
            pm=pm,
            project_name=project_name,
            spec=spec,
            resource_id=resource_id,
            mutation=mutation_box[0],
        )

    return await _finalize_formal_image_task(
        project_path=pm.get_project_path(project_name),
        resource_type=spec.bucket_key,
        resource_id=resource_id,
        script_file=None,
        artifact_path=sheet_path,
        generator=generator,
        version=version,
        task_id=task_id,
        basis=basis,
        commit_current=_commit_current,
        commit_tracked=_commit_tracked,
        missing_receipt_error="task-aware asset registration did not return a receipt",
    )


def _storyboard_formal_image_callback(
    *,
    project_name: str,
    script_file: str,
    resource_id: str,
    artifact_path: str,
    prompt: str,
    versions: Any,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
    outcome_box: list[_FormalImageCommitOutcome],
    project_manager: ProjectManager | None = None,
) -> _StagedImageCommit:
    """Build a staged storyboard activation using the shared formal image seam."""

    pm = project_manager or get_project_manager()
    project_path = pm.get_project_path(project_name)

    def _commit_metadata(activate: Callable[[], None]) -> _MetadataCompensator | None:
        mutation_box: list[OptimisticMappingMemberPatch] = []

        def _activate(_script_path: Path) -> None:
            activate()

        _write_storyboard_image_metadata(
            pm=pm,
            project_name=project_name,
            script_file=script_file,
            resource_id=resource_id,
            artifact_path=artifact_path,
            mutation_box=mutation_box,
            on_commit=_activate,
        )
        if task_id is None:
            return None
        return _storyboard_metadata_compensator(
            pm=pm,
            project_name=project_name,
            script_file=script_file,
            resource_id=resource_id,
            mutation=mutation_box[0],
        )

    return _staged_formal_image_callback(
        versions=versions,
        project_path=project_path,
        resource_type="storyboards",
        resource_id=resource_id,
        script_file=script_file,
        artifact_path=artifact_path,
        prompt=prompt,
        task_id=task_id,
        basis=basis,
        outcome_box=outcome_box,
        commit_metadata=_commit_metadata,
    )


async def _finalize_storyboard_image_task(
    *,
    project_name: str,
    script_file: str,
    resource_id: str,
    artifact_path: str,
    generator: Any,
    version: int,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None = None,
    project_manager: ProjectManager | None = None,
) -> tuple[str, _CancellationReceipt | None]:
    """Commit storyboard metadata and image selection through one shared seam."""

    pm = project_manager or get_project_manager()

    def _commit_current(register: Callable[[Path], None]) -> None:
        pm.update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="storyboard_image",
            asset_path=artifact_path,
            on_commit=register,
        )

    def _commit_tracked(register: Callable[[Path], None]) -> _MetadataCompensator:
        mutation_box: list[OptimisticMappingMemberPatch] = []
        _write_storyboard_image_metadata(
            pm=pm,
            project_name=project_name,
            script_file=script_file,
            resource_id=resource_id,
            artifact_path=artifact_path,
            mutation_box=mutation_box,
            on_commit=register,
        )
        return _storyboard_metadata_compensator(
            pm=pm,
            project_name=project_name,
            script_file=script_file,
            resource_id=resource_id,
            mutation=mutation_box[0],
        )

    return await _finalize_formal_image_task(
        project_path=pm.get_project_path(project_name),
        resource_type="storyboards",
        resource_id=resource_id,
        script_file=script_file,
        artifact_path=artifact_path,
        generator=generator,
        version=version,
        task_id=task_id,
        basis=basis,
        commit_current=_commit_current,
        commit_tracked=_commit_tracked,
        missing_receipt_error="task-aware storyboard registration did not return a receipt",
    )


@dataclass(frozen=True, slots=True)
class _FormalImagePlan:
    """Per-task differences of the shared formal image submit/activate pipeline."""

    resource_type: str
    resource_id: str
    artifact_path: str
    prompt: str
    aspect_ratio: str
    build_commit_callback: Callable[[Any, list[_FormalImageCommitOutcome]], _StagedImageCommit]
    finalize: Callable[[Any, int], Awaitable[tuple[str, _CancellationReceipt | None]]]
    pre_submit: Callable[[], Awaitable[None]] | None = None
    before_submit: Callable[[], Awaitable[None]] | None = None


async def _run_formal_image_task(
    *,
    project_name: str,
    payload: dict[str, Any],
    project: dict[str, Any],
    user_id: str,
    task_id: str | None,
    frozen_references: FrozenImageReferences,
    plan: _FormalImagePlan,
) -> dict[str, Any]:
    """Submit one formal image, then take either the staged activation or the finalizer path."""

    reference_images = frozen_references.reference_images
    formal_outcomes: list[_FormalImageCommitOutcome] = []

    async def _submit() -> tuple[Any, tuple[Path, int]]:
        ctx = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            user_id=user_id,
            image=ImageLaneRequest(capability="i2i" if reference_images else "t2i"),
        )
        if plan.pre_submit is not None:
            await plan.pre_submit()
        generator = ctx.generator
        # before_submit 只在声明了 checkpoint 的任务上出现，不声明就不落进 kwargs
        optional: dict[str, Any] = {}
        if plan.before_submit is not None:
            optional["before_submit"] = plan.before_submit
        generated = await generator.generate_image_async(
            prompt=plan.prompt,
            resource_type=plan.resource_type,
            resource_id=plan.resource_id,
            reference_images=reference_images,
            aspect_ratio=plan.aspect_ratio,
            image_size=ctx.image.resolution,
            formal_output=True,
            task_id=_formal_image_task_token(task_id),
            commit_formal_output=plan.build_commit_callback(generator, formal_outcomes),
            **optional,
        )
        return generator, generated

    try:
        generator, (_generated_path, version) = await _submit()
    finally:
        await run_noninterruptible_sync(frozen_references.cleanup)

    if formal_outcomes:
        outcome = formal_outcomes[0]
        version, created_at, receipt = outcome.version, outcome.created_at, outcome.receipt
    else:
        created_at, receipt = await plan.finalize(generator, version)

    return compensable_formal_task_result(
        {
            "version": version,
            "file_path": plan.artifact_path,
            "created_at": created_at,
            "resource_type": plan.resource_type,
            "resource_id": plan.resource_id,
        },
        receipt,
    )


async def _run_asset_sheet_image_task(
    *,
    asset_type: str,
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    user_id: str,
    task_id: str | None,
    project: dict[str, Any],
    full_prompt: str,
    frozen_references: FrozenImageReferences,
    basis: ArtifactBasis | ArtifactBasisDescriptor | None,
) -> dict[str, Any]:
    """Run the submit/activate pipeline shared by every asset-sheet image task."""

    bucket_key = ASSET_SPECS[asset_type].bucket_key
    sheet_path = f"{bucket_key}/{resource_id}.png"

    def _build_commit(generator: Any, outcome_box: list[_FormalImageCommitOutcome]) -> _StagedImageCommit:
        return _asset_sheet_formal_image_callback(
            asset_type=asset_type,
            project_name=project_name,
            resource_id=resource_id,
            sheet_path=sheet_path,
            prompt=full_prompt,
            versions=generator.versions,
            task_id=task_id,
            basis=basis,
            outcome_box=outcome_box,
        )

    async def _finalize(generator: Any, version: int) -> tuple[str, _CancellationReceipt | None]:
        return await _finalize_asset_sheet_task(
            asset_type=asset_type,
            project_name=project_name,
            resource_id=resource_id,
            sheet_path=sheet_path,
            generator=generator,
            version=version,
            task_id=task_id,
            basis=basis,
        )

    return await _run_formal_image_task(
        project_name=project_name,
        payload=payload,
        project=project,
        user_id=user_id,
        task_id=task_id,
        frozen_references=frozen_references,
        plan=_FormalImagePlan(
            resource_type=bucket_key,
            resource_id=resource_id,
            artifact_path=sheet_path,
            prompt=full_prompt,
            aspect_ratio=get_aspect_ratio(project, bucket_key),
            build_commit_callback=_build_commit,
            finalize=_finalize,
        ),
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


# (entity_type, action, label_key, include_script_episode)
# label_key 是事件载荷携带的稳定标识，界面按用户语言查表成文；文案本身见 lib/i18n 的
# ``event_label_*``。三类项目级资产（character / scene / prop）的 spec 由
# lib.asset_types.ASSET_SPECS 派生。
# storyboard / video / reference_video 不在此表——三者按剧本骨架种类（segments/scenes/shots/
# video_units）动态派生 entity_type 与条目名词，见 _SKELETON_DRIVEN_TASK_ACTIONS，避免恒发
# ``segment``/「分镜」而与分镜级事件（project_events.py）名词不一致。
_TASK_CHANGE_SPECS: dict[str, tuple] = {
    "grid": ("grid", "grid_ready", "grid", True),
    "grid_split": ("grid", "grid_split_done", "grid_split", True),
    "voice_sample": ("character", "voice_sample_ready", "voice_sample", False),
    **{atype: (atype, "updated", f"asset_image_{atype}", False) for atype in ASSET_SPECS},
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

    def _prepare():
        _project = get_project_manager().load_project(project_name)
        _project_path = get_project_manager().get_project_path(project_name)
        _script = get_project_manager().load_script(project_name, script_file)
        _script_input = resolve_usable_episode_script_input(
            project_path=_project_path,
            project=_project,
            script=_script,
            script_filename=str(script_file),
        )
        _artifact_episode = _script_input.episode
        _currency_resolver = active_artifact_currency_resolver(_project_path, _project)
        _formal_claims: list[ArtifactInputClaim] = [_script_input.claim]
        _items, _id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(_script)

        _resolved = find_storyboard_item(_items, _id_field, resource_id)
        if _resolved is None:
            raise ValueError(f"scene/segment not found: {resource_id}")
        _target_item, _target_index = _resolved
        _semantic_prompt = _target_item.get("image_prompt")

        _prev_path = resolve_previous_storyboard_path(_project_path, _items, _id_field, resource_id)
        _previous_id = (
            str(_items[_target_index - 1].get(_id_field) or "")
            if _prev_path is not None and _target_index > 0
            else None
        )
        _style = _project.get("style", "")
        _style_description = _project.get("style_description", "")
        if not isinstance(_style, str) or not isinstance(_style_description, str):
            raise ValueError("storyboard style and style description must be strings")
        _prompt_text = _normalize_storyboard_prompt(_semantic_prompt, _style, _style_description)
        _visual_references: list[VisualReference] = []
        _ref_images = _collect_reference_images(
            _project,
            _project_path,
            _target_item,
            char_field=_char_field,
            scene_field=_scene_field,
            prop_field=_prop_field,
            extra_reference_images=payload.get("extra_reference_images") or [],
            previous_storyboard_path=_prev_path,
            previous_storyboard_id=_previous_id,
            visual_references=_visual_references,
            artifact_episode=_artifact_episode,
            currency_resolver=_currency_resolver,
            formal_claims=_formal_claims,
        )
        # 商品分镜：商品参考全量注入且排序绝对优先（先于角色/场景/道具 sheet），
        # 并附高保真还原指令；氛围分镜零商品图，既有装配不变。
        _product_refs = _collect_shot_product_references(
            _project,
            _project_path,
            _target_item,
            currency_resolver=_currency_resolver,
            formal_claims=_formal_claims,
        )
        if _product_refs:
            _ref_images = _product_refs + (_ref_images or [])
            _visual_references = _product_visual_references(_product_refs) + _visual_references
            _prompt_text = append_product_fidelity_tail(_prompt_text, _product_names_in_references(_product_refs))
        _frozen = freeze_image_references(_ref_images, _visual_references)
        try:
            _formal_claims = list(
                bind_artifact_input_claims_to_frozen_visuals(
                    project_path=_project_path,
                    resolver=_currency_resolver,
                    claims=_formal_claims,
                    source_references=_visual_references,
                    frozen_references=_frozen.visual_references,
                )
            )
            _basis = build_storyboard_image_visual_basis(
                resource_id=resource_id,
                image_prompt=_semantic_prompt,
                style=_style,
                style_description=_style_description,
                aspect_ratio=get_aspect_ratio(_project, "storyboards"),
                references=_frozen.visual_references,
            )
        except BaseException:
            _frozen.cleanup()
            raise
        return _project, _project_path, _prompt_text, _frozen, _basis, tuple(_formal_claims)

    project, project_path, prompt_text, frozen_references, storyboard_basis, formal_claims = await asyncio.to_thread(
        _prepare
    )
    artifact_path = f"storyboards/scene_{resource_id}.png"

    async def _assert_claims_usable() -> None:
        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

    def _build_commit(generator: Any, outcome_box: list[_FormalImageCommitOutcome]) -> _StagedImageCommit:
        return _storyboard_formal_image_callback(
            project_name=project_name,
            script_file=str(script_file),
            resource_id=resource_id,
            artifact_path=artifact_path,
            prompt=prompt_text,
            versions=generator.versions,
            task_id=task_id,
            basis=storyboard_basis,
            outcome_box=outcome_box,
        )

    async def _finalize(generator: Any, version: int) -> tuple[str, _CancellationReceipt | None]:
        return await _finalize_storyboard_image_task(
            project_name=project_name,
            script_file=str(script_file),
            resource_id=resource_id,
            artifact_path=artifact_path,
            generator=generator,
            version=version,
            task_id=task_id,
            basis=storyboard_basis,
        )

    return await _run_formal_image_task(
        project_name=project_name,
        payload=payload,
        project=project,
        user_id=user_id,
        task_id=task_id,
        frozen_references=frozen_references,
        plan=_FormalImagePlan(
            resource_type="storyboards",
            resource_id=resource_id,
            artifact_path=artifact_path,
            prompt=prompt_text,
            aspect_ratio=get_aspect_ratio(project, "storyboards"),
            build_commit_callback=_build_commit,
            finalize=_finalize,
            pre_submit=_assert_claims_usable,
            before_submit=_assert_claims_usable,
        ),
    )


def _resolve_tts_task_items(
    script: dict[str, Any],
    *,
    reference_video_route: bool,
) -> tuple[list[dict[str, Any]], str, str]:
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
    tts_selection_error: BaseException | None = None
    tts_settings_bridge = EventLoopBridge.capture()
    selected_current = True
    missing_narration_audio = object()
    prior_narration_audio: object = missing_narration_audio
    prior_manifest_entry: ArtifactManifestEntry | None = None
    prior_manifest_captured = False

    class _TtsSelectionResolutionFailed(RuntimeError):
        pass

    async def _measure_staged(staged_path: Path) -> None:
        nonlocal duration_seconds, tts_selection_error
        try:
            measured_duration = await probe_existing_audio_duration_seconds(staged_path)
        except (Exception, asyncio.CancelledError) as exc:
            tts_selection_error = exc
            return
        if measured_duration is None or not math.isfinite(measured_duration) or measured_duration <= 0:
            tts_selection_error = RuntimeError("generated narration audio duration is unavailable")
            return
        duration_seconds = float(measured_duration)

    def _commit_staged(staged_path: Path, output_path: Path) -> int | PaidVersionCommit:
        nonlocal prior_narration_audio, selected_current, tts_selection_error
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
        committed_outcome: PaidVersionCommit | None = None
        should_select = False
        guarded_project: dict[str, Any] | None = None

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

        if tts_selection_error is not None:
            committed_outcome = _archive_paid_history()
            selected_current = False
            return committed_outcome

        def _register_basis() -> None:
            register_narration_audio_transactionally(
                project_path=project_path,
                episode=committed_episode,
                preparation=committed_preparation,
                settings=settings,
            )

        def _activate(_script_path: Path) -> None:
            nonlocal committed_outcome, prior_manifest_captured, prior_manifest_entry
            if should_select:
                manifest_adapter = ProjectArtifactManifestAdapter(project_path)
                prior_manifest_entry = manifest_adapter.get_entry(
                    ArtifactKey.episode_audio(committed_episode, resource_id)
                )
                prior_manifest_captured = True
            committed_outcome = generator.versions.commit_staged_paid_version(
                resource_type="audio",
                resource_id=resource_id,
                prompt=text,
                staged_file=staged_path,
                current_file=output_path,
                select_current=lambda: should_select,
                on_select=_register_basis,
                **version_metadata,
            )

        def _same_script(_project: dict) -> str:
            nonlocal guarded_project
            guarded_project = _project
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
                if guarded_project is None:
                    raise RuntimeError("TTS commit guard did not expose the current project")
                try:
                    current_commit_settings = tts_settings_bridge.run(
                        CurrentTtsSettingsResolver(
                            project_name,
                            user_id=user_id,
                            project_path=project_path,
                            context_resolver=resolve_generation_context,
                        ).resolve_tts_synthesis_settings(guarded_project)
                    )
                except (Exception, asyncio.CancelledError) as exc:
                    tts_selection_error = exc
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
                if tts_selection_error is None and item is not None:
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
                    prior_narration_audio = (
                        copy.deepcopy(assets["narration_audio"])
                        if isinstance(assets, dict) and "narration_audio" in assets
                        else missing_narration_audio
                    )
                    if not isinstance(assets, dict):
                        assets = ProjectManager.create_generated_assets(
                            str(current_script.get("content_mode") or "narration")
                        )
                        item["generated_assets"] = assets
                    assets["narration_audio"] = audio_rel
                    pm.update_scene_status(item)
        except (EpisodeScriptReboundError, _TtsSelectionResolutionFailed):
            committed_outcome = _archive_paid_history()
        except BaseException as failure:
            if staged_path.is_file():
                try:
                    _archive_paid_history()
                except BaseException as archive_failure:
                    failure.add_note(f"paid TTS history archival also failed: {archive_failure}")
            raise

        if committed_outcome is None:
            raise RuntimeError("TTS commit completed without a version")
        selected_current = committed_outcome.selected
        return committed_outcome

    async def _before_submit() -> None:
        await asyncio.to_thread(
            assert_current_artifact_input_claims_usable,
            project_path,
            formal_input_claims,
        )

    output_path, version = await generator.generate_audio_async(
        text=text,
        resource_id=resource_id,
        voice=voice,
        speed=speed,
        before_submit=_before_submit,
        before_commit=_measure_staged,
        commit_staged=_commit_staged,
        tts_provider_id=settings.provider_id,
        tts_model_id=settings.model_id,
        tts_voice=settings.voice,
        tts_speed=settings.speed,
        tts_basis_digest=basis.digest if basis is not None else None,
    )

    if tts_selection_error is not None:
        raise tts_selection_error

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

    result = {
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
    if task_id is None or not selected_current:
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
        cancelled_episode = episode

        def _same_script(_project: dict) -> str:
            current_binding = resolve_episode_script_binding(_project, cancelled_episode, str(script_file))
            if current_binding is None:
                raise EpisodeScriptReboundError(
                    f"episode {cancelled_episode} script binding changed before TTS cancellation"
                )
            return current_binding

        try:
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
                if item is not None:
                    assets = item.get("generated_assets")
                    if not isinstance(assets, dict):
                        assets = ProjectManager.create_generated_assets(
                            str(current_script.get("content_mode") or "narration")
                        )
                        item["generated_assets"] = assets
                    if assets.get("narration_audio") != audio_rel:
                        raise RuntimeError("narration audio changed before cancellation compensation")
                    if prior_narration_audio is missing_narration_audio:
                        assets.pop("narration_audio", None)
                    else:
                        assets["narration_audio"] = copy.deepcopy(prior_narration_audio)
                    pm.update_scene_status(item)
        except EpisodeScriptReboundError:
            # The old script is no longer the episode's current edit target, but
            # cancellation must still revoke this task's formal media selection.
            _reject_with_manifest_restore()

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

    project, project_path, item, content_mode, script_kind, script, script_input = await asyncio.to_thread(_load)
    execution_input_authority = payload.get("execution_input_authority", "current-script")
    if execution_input_authority not in {"current-script", "request"}:
        raise ValueError("execution_input_authority must be 'current-script' or 'request'")
    request_authoritative = task_id is None or execution_input_authority == "request"
    # Interactive queue execution re-materializes mutable visual intent from the current script unit. Exact
    # external requests opt into their queued prompt and duration so a later script edit cannot replace approved
    # provider inputs before the worker reaches the paid effect boundary.
    current_prompt = item.get("video_prompt") if isinstance(item, dict) else None
    prompt = payload.get("prompt", current_prompt) if request_authoritative else current_prompt
    if prompt is None:
        raise ValueError("current script unit is missing video_prompt")
    requested_visual_prompt = copy.deepcopy(prompt)
    delivery_options = NarrationDeliveryRequestOptions.from_payload(payload)
    video_input_mode = payload.get("video_input_mode", "storyboard")
    if video_input_mode not in {"storyboard", "text"}:
        raise ValueError("video_input_mode must be 'storyboard' or 'text'")
    raw_output_normalization = payload.get("video_output_normalization")
    output_normalization = (
        VideoOutputNormalizationPolicy.from_dict(raw_output_normalization).to_dict()
        if raw_output_normalization is not None
        else None
    )
    # lane 归桶按项目生成模式求值，与提交入口（``generate_video``）同源：入口挡掉参考生视频后
    # 到达这里的项目恒为 i2v，但桶不在两处各硬编码一次，避免生成模式口径分叉。
    execution_payload = without_video_execution_identity(payload) if task_id is not None else payload
    ctx = await resolve_generation_context(
        project_name,
        execution_payload,
        project=project,
        user_id=user_id,
        video=VideoLaneRequest(capability=video_bucket_for_generation_mode(project.get("generation_mode"))),
        audio=AudioLaneRequest() if delivery_options.narration_delivery == USE_TTS else None,
    )
    generator = ctx.generator
    registry_provider_id = ctx.video.provider_model.provider_id
    if claimed_provider_id is not None and registry_provider_id != claimed_provider_id:
        raise DispatchProviderChanged(
            claimed_provider_id=claimed_provider_id,
            actual_provider_id=registry_provider_id,
        )
    model_name = ctx.video.backend_model
    supported_durations: list[int] = list(ctx.video.supported_durations)
    resolution = ctx.video.resolution

    artifact_episode = script_input.episode
    formal_input_claims: list[ArtifactInputClaim] = [script_input.claim]
    currency_resolver = active_artifact_currency_resolver(project_path, project)
    storyboard_file: Path | None = None
    end_image: Path | None = None
    if video_input_mode == "storyboard":
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

    def _visual_basis_digest_for(storyboard_image: Path | None, end_frame_image: Path | None) -> str:
        common = {
            "prompt": requested_visual_prompt,
            "aspect_ratio": aspect_ratio,
            "provider_id": registry_provider_id,
            "model_id": model_name,
            "resolution": resolution,
            "seed": seed,
            "requested_generate_audio": ctx.video.requested_generate_audio,
            "content_mode": content_mode,
            "utterances": item.get("utterances") if content_mode == "drama" else None,
            "has_utterances": content_mode == "drama" and "utterances" in item,
            "voice_characters": (None if ctx.video.is_silent else project.get("characters"))
            if content_mode == "drama"
            else None,
        }
        if video_input_mode == "text":
            return build_text_video_visual_basis(**common).digest
        if storyboard_image is None:
            raise ValueError("storyboard video input is missing its start image")
        return build_storyboard_video_visual_basis(
            storyboard_image=storyboard_image,
            end_frame_image=end_frame_image,
            **common,
        ).digest

    def _current_visual_basis_digest() -> str:
        return _visual_basis_digest_for(storyboard_file, end_image)

    visual_basis_digest = await asyncio.to_thread(_current_visual_basis_digest)

    # Voice_Profiles 声明段唯一来源是下方 build_drama_video_prompt 的机械派生：调用方（WebUI
    # 请求体 / 剧本 JSON 残留）自带的 voice_profiles 一律先剥离，不因 utterances 门控不触发
    # （narration/ad、或 drama 无 utterances 的条目）而绕过 C 类（真无声）门控直达 YAML。
    if isinstance(prompt, dict):
        prompt = strip_voice_profiles(prompt)

    # drama 口型台词单一真相源在分镜级有序 utterances：从 dialogue-kind 条目取台词注入 video YAML
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
    duration_seconds = (
        payload.get("duration_seconds")
        if request_authoritative
        else item.get("duration_seconds")
        if isinstance(item, dict)
        else None
    )
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
        episode = artifact_episode
        if episode is None:
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
    duration_seconds = int(float(duration_seconds))

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
            revalidate_visual_basis_digest=_current_visual_basis_digest,
        )
        if reused is not None:
            return reused

    provider_start_image = storyboard_file
    provider_end_image = end_image

    async def _admit_before_submit(_api_call_id: int) -> Mapping[str, object] | None:
        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_input_claims)
        return None

    checkpoint_hook: Callable[[int], Awaitable[Mapping[str, object] | None]] | None = _admit_before_submit
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
                    *constrain_durations(
                        registry_provider_id,
                        model_name,
                        supported_durations,
                        resolution=resolution,
                    ),
                }
            )
        )
        media_inputs: list[ProviderMediaInput] = []
        if video_input_mode == "storyboard":
            if storyboard_file is None:
                raise ValueError("storyboard video input is missing its start image")
            media_inputs.append(
                ProviderMediaInput(
                    path=storyboard_file,
                    role="start_image",
                    logical_type="storyboard",
                    logical_name=resource_id,
                    kind="first_frame",
                )
            )
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
        try:
            if media_inputs:
                staged_media = await stage_provider_media_for_task(project_path, task_id, tuple(media_inputs))
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
            if video_input_mode == "text":
                artifact_visual_basis = await asyncio.to_thread(
                    lambda: build_text_video_artifact_visual_basis(
                        resource_id=resource_id,
                        visual_prompt=requested_visual_prompt,
                        aspect_ratio=aspect_ratio,
                    )
                )
            else:
                if provider_start_image is None:
                    raise ValueError("storyboard video input is missing its staged start image")
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

            async def _checkpoint_before_submit(api_call_id: int) -> Mapping[str, object]:
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
                checkpoint_type = (
                    TextVideoSubmissionCheckpoint if video_input_mode == "text" else StoryboardSubmissionCheckpoint
                )
                checkpoint = checkpoint_type.create(
                    task_id=task_id,
                    project_name=project_name,
                    script_file=script_file,
                    unit_id=resource_id,
                    capability="i2v",
                    provider_id=ctx.video.provider_model.provider_id,
                    provider_model_id=ctx.video.provider_model.model_id,
                    backend_model_id=ctx.video.backend_model,
                    endpoint_guard=ctx.video.endpoint,
                    api_call_id=api_call_id,
                    prompt=prompt_text,
                    duration_seconds=duration_seconds,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    generate_audio=ctx.video.requested_generate_audio,
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
    normalization_metadata: dict[str, Any] = {}
    if output_normalization is not None:
        normalization_metadata["video_output_normalization"] = output_normalization
    try:
        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_input_claims)
        output_path, version, _, video_uri = await generator.generate_video_async(
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
            generate_audio=ctx.video.requested_generate_audio,
            **normalization_metadata,
        )

        async def _finalize() -> dict[str, Any]:
            return await _finalize_video_task(
                project_name=project_name,
                script_file=script_file,
                project_path=project_path,
                resource_id=resource_id,
                version=version,
                video_uri=video_uri,
                generator=generator,
            )

        return await complete_video_artifact_commit(
            committer=artifact_committer,
            versions=generator.versions,
            resource_type="videos",
            resource_id=resource_id,
            version=version,
            video_uri=video_uri,
            finalize=_finalize,
        )
    finally:
        if artifact_committer is not None:
            await artifact_committer.release_admission_guard()
        if task_id is not None:
            await asyncio.to_thread(cleanup_staged_provider_media, project_path, task_id)


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
        _visual_references = tuple(
            VisualReference(
                path=path,
                role="source",
                logical_type="character",
                logical_id=resource_id,
                kind="original",
            )
            for path in (_ref_images or [])
        )
        _frozen = freeze_image_references(_ref_images, _visual_references)
        try:
            _basis = build_asset_sheet_visual_basis(
                asset_type="character",
                asset_id=resource_id,
                description=prompt,
                style=str(_style or ""),
                style_description=str(_style_desc or ""),
                aspect_ratio="16:9",
                references=_frozen.visual_references,
            )
        except BaseException:
            _frozen.cleanup()
            raise
        return _project, _full_prompt, _frozen, _basis

    project, full_prompt, frozen_references, basis = await asyncio.to_thread(_prepare_char)
    return await _run_asset_sheet_image_task(
        asset_type="character",
        project_name=project_name,
        resource_id=resource_id,
        payload=payload,
        user_id=user_id,
        task_id=task_id,
        project=project,
        full_prompt=full_prompt,
        frozen_references=frozen_references,
        basis=basis,
    )


# 仅保留 design 任务的「prompt 构造器」差异；bucket_key 与 sheet 写入由 ASSET_SPECS 与
# ProjectManager._update_asset_sheet 统一派发。
_DESIGN_PROMPT_BUILDERS: dict[str, Any] = {
    "scene": build_scene_prompt,
    "prop": build_prop_prompt,
    "product": build_product_prompt,
}


def _collect_product_reference_images(project: dict, project_path: Path, resource_id: str) -> list[Path] | None:
    """商品原图（保真验收锚点）作为 sheet 标准化整理的参考输入；缺失文件跳过。"""
    entry = normalize_asset_bucket(project.get("products")).get(normalize_asset_name(resource_id)) or {}
    refs = entry.get("reference_images")
    if not isinstance(refs, list):
        return None
    # safe_exists 同时兜住脏数据（非字符串）、越出项目目录的绝对路径 / `..` 穿越与文件缺失
    existing = [project_path / ref for ref in refs if safe_exists(project_path, ref)]
    if refs and not existing:
        # 声明了原图却全部缺失：下游（sheet 生成 / 分镜保真注入）静默退化会丢失保真锚定，
        # 留观测痕迹便于诊断（不阻塞——文件缺失可能是归档迁移等正常历史原因）。
        # 文案保持场景中立：本函数同时服务 sheet 生成与商品分镜参考收集两个调用方。
        logger.warning("商品 '%s' 声明了 %d 张原图但磁盘均缺失", resource_id, len(refs))
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
    task_id: str | None = None,
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
        visual_references = tuple(
            VisualReference(
                path=path,
                role="source",
                logical_type=kind,
                logical_id=resource_id,
                kind="original",
            )
            for path in (refs or [])
        )
        frozen = freeze_image_references(refs, visual_references)
        try:
            basis = build_asset_sheet_visual_basis(
                asset_type=kind,
                asset_id=resource_id,
                description=prompt,
                style=str(style or ""),
                style_description=str(style_desc or ""),
                aspect_ratio="16:9",
                references=frozen.visual_references,
            )
        except BaseException:
            frozen.cleanup()
            raise
        return project, full_prompt, frozen, basis

    project, full_prompt, frozen_references, basis = await asyncio.to_thread(_prepare)
    return await _run_asset_sheet_image_task(
        asset_type=kind,
        project_name=project_name,
        resource_id=resource_id,
        payload=payload,
        user_id=user_id,
        task_id=task_id,
        project=project,
        full_prompt=full_prompt,
        frozen_references=frozen_references,
        basis=basis,
    )


async def execute_scene_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_design_task("scene", project_name, resource_id, payload, user_id=user_id, task_id=task_id)


async def execute_prop_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_design_task("prop", project_name, resource_id, payload, user_id=user_id, task_id=task_id)


async def execute_product_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    return await execute_design_task("product", project_name, resource_id, payload, user_id=user_id, task_id=task_id)


def _group_scenes_by_segment_break(items: list[dict], id_field: str) -> list[list[dict]]:
    """Groups consecutive scene dicts, breaking at segment_break=True.

    Delegates to :func:`lib.storyboard_sequence.group_scenes_by_segment_break`.
    """
    return group_scenes_by_segment_break(items, id_field)


def _collect_grid_reference_images(
    project_path: Path,
    payload: dict[str, Any],
    scene_ids: list[str],
    *,
    project: dict[str, Any] | None = None,
    script: dict[str, Any] | None = None,
    currency_resolver: ArtifactCurrencyResolver,
    formal_claims: list[ArtifactInputClaim] | None = None,
    visual_references: list[VisualReference] | None = None,
) -> tuple[list[object] | None, list[dict]]:
    """Collect character/scene/prop sheet images referenced by grid scenes.

    Returns a tuple of ``(image_paths, metadata)``:
    - *image_paths*: up to 6 :class:`~pathlib.Path` objects for the generation API.
    - *metadata*: list of dicts ``{path, name, ref_type}`` for persisting in
      :class:`~lib.grid.models.GridGeneration`.
    """
    if project is None:
        project_json = project_path / "project.json"
        if not project_json.exists():
            return None, []
        import json

        loaded_project = json.loads(project_json.read_text(encoding="utf-8"))
        if not isinstance(loaded_project, dict):
            return None, []
        project = loaded_project

    script_file = payload.get("script_file")
    if not script_file:
        return None, []

    if script is None:
        script_path = project_path / "scripts" / script_file
        if not script_path.exists():
            return None, []
        import json

        loaded_script = json.loads(script_path.read_text(encoding="utf-8"))
        if not isinstance(loaded_script, dict):
            return None, []
        script = loaded_script

    items, id_field, char_field, scene_field, prop_field = get_storyboard_items(script)

    scene_id_set = set(scene_ids)
    matched_items = [item for item in items if str(item.get(id_field, "")) in scene_id_set]
    selected_visuals: list[VisualReference] = []
    references, _seen = _collect_sheet_references(
        project,
        project_path,
        matched_items,
        char_field=char_field,
        scene_field=scene_field,
        prop_field=prop_field,
        max_count=6,
        visual_references=selected_visuals,
        currency_resolver=currency_resolver,
        formal_claims=formal_claims,
    )
    if visual_references is not None:
        visual_references.extend(selected_visuals)
    metadata = [
        {
            "path": reference.path.relative_to(project_path).as_posix(),
            "name": provider["label"],
            "ref_type": reference.logical_type,
        }
        for provider, reference in zip(references, selected_visuals, strict=True)
    ]
    return [reference["image"] for reference in references] or None, metadata


def _grid_metadata_compensator(
    *,
    grid_manager: Any,
    resource_id: str,
    mutation: OptimisticMappingPatch,
) -> _MetadataCompensator:
    """Roll the grid record back to the pre-write patch inside the rejecting transaction."""

    def _compensate_metadata(reject: Callable[[], None]) -> None:
        def _restore(current: Any) -> None:
            current_data = current.to_dict()
            mutation.restore(current_data)
            restored = type(current).from_dict(current_data)
            current.__dict__.update(restored.__dict__)

        restored = grid_manager.update(resource_id, _restore, on_commit=reject)
        if restored is None:
            reject()

    return _compensate_metadata


def _grid_formal_image_callback(
    *,
    project_path: Path,
    grid_manager: Any,
    grid: Any,
    initial_grid: Mapping[str, Any],
    resource_id: str,
    prompt: str,
    versions: Any,
    task_id: str | None,
    basis: ArtifactBasis | ArtifactBasisDescriptor,
    outcome_box: list[_FormalImageCommitOutcome],
) -> _StagedImageCommit:
    """Build a staged grid-composite activation through the shared image seam."""

    artifact_path = f"grids/{resource_id}.png"

    def _commit_metadata(activate: Callable[[], None]) -> _MetadataCompensator | None:
        def _complete(current_grid: Any) -> None:
            current_grid.grid_image_path = artifact_path
            current_grid.status = "completed"
            current_grid.split_at = None

        committed_grid = grid_manager.update_formal(resource_id, _complete, on_commit=activate)
        if committed_grid is None:
            raise ValueError(f"grid not found: {resource_id}")
        grid.grid_image_path = committed_grid.grid_image_path
        grid.status = committed_grid.status
        grid.split_at = committed_grid.split_at
        if task_id is None:
            return None
        return _grid_metadata_compensator(
            grid_manager=grid_manager,
            resource_id=resource_id,
            mutation=OptimisticMappingPatch.capture(initial_grid, committed_grid.to_dict()),
        )

    return _staged_formal_image_callback(
        versions=versions,
        project_path=project_path,
        resource_type="grids",
        resource_id=resource_id,
        script_file=None,
        artifact_path=artifact_path,
        prompt=prompt,
        task_id=task_id,
        basis=basis,
        outcome_box=outcome_box,
        commit_metadata=_commit_metadata,
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
    1. Load GridGeneration, set status to generating
    2. Generate the joint image via MediaGenerator (versioned as resource_type "grids")
    3. Mark completed

    只产出联合图，不触碰任何分镜格；落格由独立的切分操作
    （server.services.grid_split.apply_grid_split）显式执行。
    """
    from lib.grid.layout import GRID_FALLBACK_RESOLUTION, grid_aspect_ratio_for
    from lib.grid.prompt_builder import build_grid_prompt
    from lib.grid_manager import GridManager

    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    grid_manager = GridManager(project_path)

    # a) Load grid
    grid = grid_manager.get(resource_id)
    if grid is None:
        raise ValueError(f"grid not found: {resource_id}")
    project = await asyncio.to_thread(get_project_manager().load_project, project_name)
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
    initial_grid = copy.deepcopy(grid.to_dict())

    version: int | None = None
    generator: Any = None
    frozen_references: FrozenImageReferences | None = None
    try:
        # b) Set status to generating
        grid.status = "generating"
        grid.error_message = None
        grid_manager.save(grid)

        # c) Build reference images + metadata
        from lib.grid.models import ReferenceImage

        currency_resolver = await asyncio.to_thread(active_artifact_currency_resolver, project_path, project)
        formal_claims: list[ArtifactInputClaim] = [script_input.claim]
        visual_references: list[VisualReference] = []
        reference_images, ref_metadata = await asyncio.to_thread(
            _collect_grid_reference_images,
            project_path,
            payload,
            grid.scene_ids,
            project=project,
            script=script,
            currency_resolver=currency_resolver,
            formal_claims=formal_claims,
            visual_references=visual_references,
        )
        frozen_references = await asyncio.to_thread(
            freeze_image_references,
            reference_images,
            visual_references,
        )
        formal_claims = list(
            await asyncio.to_thread(
                bind_artifact_input_claims_to_frozen_visuals,
                project_path=project_path,
                resolver=currency_resolver,
                claims=formal_claims,
                source_references=visual_references,
                frozen_references=frozen_references.visual_references,
            )
        )
        reference_images = frozen_references.reference_images
        grid.reference_images = [ReferenceImage.from_dict(m) for m in ref_metadata] if ref_metadata else []
        grid_manager.save(grid)

        # d) Generate grid image
        _needs_i2i = bool(reference_images)
        items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
        item_by_id = {str(item.get(id_field)): item for item in items if isinstance(item, Mapping)}
        if len(set(grid.scene_ids)) != len(grid.scene_ids):
            raise ValueError("grid scene identities must be unique")
        missing_members = [scene_id for scene_id in grid.scene_ids if scene_id not in item_by_id]
        if missing_members:
            raise ValueError(f"grid scenes are no longer present in the bound script: {missing_members}")
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
            style=str(project.get("style") or ""),
            aspect_ratio=member_aspect_ratio,
            grid_aspect_ratio=grid_aspect_ratio,
        )
        grid_basis = build_grid_composite_visual_basis(
            group_id=grid.id,
            members=members,
            rows=grid.rows,
            columns=grid.cols,
            style=str(project.get("style") or ""),
            grid_aspect_ratio=grid_aspect_ratio,
            references=frozen_references.visual_references,
        )
        ctx = await resolve_generation_context(
            project_name,
            payload,
            project=project,
            user_id=user_id,
            image=ImageLaneRequest(capability="i2i" if _needs_i2i else "t2i"),
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
        formal_outcomes: list[_FormalImageCommitOutcome] = []

        await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

        async def _before_submit() -> None:
            await asyncio.to_thread(assert_current_artifact_input_claims_usable, project_path, formal_claims)

        _image_path, version = await generator.generate_image_async(
            prompt=prompt_text,
            resource_type="grids",
            resource_id=resource_id,
            reference_images=reference_images,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            before_submit=_before_submit,
            formal_output=True,
            task_id=_formal_image_task_token(task_id),
            commit_formal_output=_grid_formal_image_callback(
                project_path=project_path,
                grid_manager=grid_manager,
                grid=grid,
                initial_grid=initial_grid,
                resource_id=resource_id,
                prompt=str(prompt_text),
                versions=generator.versions,
                task_id=task_id,
                basis=grid_basis,
                outcome_box=formal_outcomes,
            ),
        )

        # e) Mark joint image ready；联合图内容已更新，旧的落格结果不再对应当前图，
        # split_at 清空表示「待显式切分」。
        def _commit_grid() -> _CancellationReceipt | None:
            assert grid is not None
            manifest_box: list[ArtifactRegistrationReceipt | None] = []

            def _complete(current_grid) -> None:
                current_grid.grid_image_path = f"grids/{resource_id}.png"
                current_grid.status = "completed"
                current_grid.split_at = None

            def _register() -> None:
                manifest_box.append(
                    register_formal_task_artifact(
                        project_path,
                        resource_type="grids",
                        resource_id=resource_id,
                        script_file=None,
                        task_id=task_id,
                        artifact_path=f"grids/{resource_id}.png",
                        basis=grid_basis,
                    )
                )

            committed_grid = grid_manager.update_formal(resource_id, _complete, on_commit=_register)
            if committed_grid is None:
                raise ValueError(f"grid not found: {resource_id}")
            grid.grid_image_path = committed_grid.grid_image_path
            grid.status = committed_grid.status
            grid.split_at = committed_grid.split_at
            manifest = manifest_box[0]
            if task_id is None:
                return manifest
            if manifest is None:
                raise RuntimeError("task-aware grid registration did not return a receipt")
            if version is None:
                raise RuntimeError("grid generation did not return a selected version")
            _compensate_metadata = _grid_metadata_compensator(
                grid_manager=grid_manager,
                resource_id=resource_id,
                mutation=OptimisticMappingPatch.capture(initial_grid, grid.to_dict()),
            )
            return SelectedImageArtifactReceipt(
                versions=generator.versions,
                resource_type="grids",
                resource_id=resource_id,
                version=version,
                current_file=project_path / "grids" / f"{resource_id}.png",
                manifest=manifest,
                compensate_metadata=_compensate_metadata,
            )

        if formal_outcomes:
            outcome = formal_outcomes[0]
            version, receipt = outcome.version, outcome.receipt
        else:
            receipt = await run_formal_task_finalizer(_commit_grid, task_id=task_id)

    except Exception as failure:
        if version is not None and generator is not None:
            try:
                rejected = await asyncio.to_thread(
                    generator.versions.reject_current_version,
                    "grids",
                    resource_id,
                    rejected_version=version,
                    current_file=project_path / "grids" / f"{resource_id}.png",
                )
                if not rejected:
                    failure.add_note("generated grid version changed before formal-write compensation")
            except Exception as compensation_failure:  # noqa: BLE001
                failure.add_note(f"generated grid compensation also failed: {compensation_failure}")
        # The formal-write transaction restored the durable grid record, but
        # ``grid`` still carries the rejected completion fields in memory.
        # Reload before recording failure so the metadata pointer continues to
        # describe whichever version compensation left selected.
        grid = grid_manager.get(resource_id) or grid
        grid.status = "failed"
        import traceback

        grid.error_message = traceback.format_exc()
        grid_manager.save(grid)
        raise
    finally:
        if frozen_references is not None:
            await run_noninterruptible_sync(frozen_references.cleanup)

    created_at = grid.created_at

    return compensable_formal_task_result(
        {
            "version": version,
            "file_path": f"grids/{resource_id}.png",
            "created_at": created_at,
            "resource_type": "grids",
            "resource_id": resource_id,
        },
        receipt,
    )


async def _execute_reference_video_task_proxy(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    script_file: str | None = None,
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
        script_file=script_file,
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
                script_file=task.get("script_file"),
                user_id=user_id,
                task_id=queue_task_id,
                claimed_provider_id=claimed_provider_id,
            )
        elif task_type == "video":
            result = await executor(
                project_name,
                resource_id,
                payload,
                script_file=task.get("script_file"),
                user_id=user_id,
                task_id=queue_task_id,
                claimed_provider_id=claimed_provider_id,
            )
        else:
            result = await executor(project_name, resource_id, payload, user_id=user_id, task_id=queue_task_id)
        try:
            emit_generation_success_batch(
                task_type=task_type,
                project_name=project_name,
                resource_id=resource_id,
                payload=payload,
            )
        except BaseException:
            if isinstance(result, CompensableGenerationResult):
                await run_noninterruptible_sync(result.compensate_cancelled)
            raise
        return result
