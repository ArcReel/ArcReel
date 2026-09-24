"""参考生视频 executor。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import (
    assert_current_artifact_input_claims_usable,
    resolve_usable_episode_script_input,
)
from lib.artifacts.artifact_manifest import compose_video_artifact_basis
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.artifacts.visual_artifact_provenance import (
    build_reference_video_artifact_visual_basis,
    project_basis_style_description,
)
from lib.config.resolver import ConfigResolver, VideoGenerationType
from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_queue import (
    DispatchProviderChanged,
    get_generation_queue,
    without_reference_video_execution_identity,
)
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    DEFAULT_PLANNED_DURATION_SECONDS,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from lib.infra.path_safety import safe_join
from lib.infra.thumbnail import extract_video_thumbnail
from lib.project.project_manager import get_project_manager
from lib.script.reference_video.artifact_selection import CurrentReferenceAssets
from lib.script.reference_video.execution_checkpoint import (
    NarrationExecutionFacts,
    ProviderMediaInput,
    ReferenceSubmissionCheckpoint,
    StagedProviderMedia,
    checkpoint_version_metadata,
    cleanup_staged_provider_media,
    stage_provider_media,
    stage_provider_media_for_task,
)
from lib.script.reference_video.prompt_render import (
    RenderedUnitPrompt,
    render_video_unit_prompt,
    resolve_reference_audio_paths,
)
from lib.script.reference_video.request_projection import (
    ReferenceProjectionBlockedError,
    ReferenceRequestOptions,
    ReferenceUnitRequestProjector,
    ResolvedReferenceAsset,
    VideoRequestFactsResult,
    hydrate_reference_assets,
    resolve_reference_assets,
    unit_reference_declarations,
)
from lib.script.reference_video.units import reference_video_bucket
from lib.script.reference_video.voice_settings import VoiceRenderSettings
from lib.script.script_editor import ScriptEditError
from lib.script.script_models import ReferenceResource
from lib.speech.narration_delivery import USE_TTS
from lib.speech.speech_artifact_provenance import build_video_duration_basis
from lib.speech.speech_composition import admit_script_unit, video_unit_replan_problems
from server.services.currency.video_artifact_currency import (
    VideoArtifactCommitter,
    complete_video_artifact_commit,
    freeze_video_speech_facts,
)
from server.services.tasks.generation_context import AudioLaneRequest, VideoLaneRequest, resolve_generation_context
from server.services.tasks.narration_delivery_tasks import (
    ResolvedTtsSettingsResolver,
    materialized_reference_video_visual_basis_digest,
    prepare_current_reference_video_request_options,
    reference_video_visual_basis_digest,
    reuse_current_video_for_tier,
    tts_task_in_progress,
)

logger = logging.getLogger(__name__)


ResolvedReferenceImage = ResolvedReferenceAsset


async def _stage_provider_media_for_task(
    project_path: Path,
    task_id: str,
    inputs: tuple[ProviderMediaInput, ...],
) -> tuple[StagedProviderMedia, ...]:
    """Bind the shared cancellation-safe staging operation to this module's patchable sync seam."""

    return await stage_provider_media_for_task(project_path, task_id, inputs, stage=stage_provider_media)


def _render_unit_prompt(
    unit: dict,
    project: dict,
    settings: VoiceRenderSettings,
    *,
    request_references: list[ReferenceResource] | None = None,
) -> RenderedUnitPrompt:
    """把 unit 的书写文稿渲染成三段论 backend prompt（见 ``lib.script.reference_video.prompt_render``）。

    负向约束由 ``reference_video/unit`` 模版第三段的 ``Avoid`` 行承担，本路径不追加反向尾词；
    ``storyboard/video`` 模版的 ``Avoid`` 行只服务图生视频路径。

    空提示词的*结构校验*已上移到入队守卫点（``TaskSpec.from_request``），两条入队路径
    （WebUI / SDK）在入队时即拒绝空提示词。此处保留一道防御性空检查，因为参考生视频的
    提示词源是*可变*的 script 文件且执行期从新读取（队列 dedup 不看 payload，无法靠入队
    快照兜底）：若提示词在入队后被改空、或在途遗留任务漏过守卫，这道检查避免空提示词被
    机器生成的第一/三段撑成非空文本绕过 backend 的空值保护、白白消耗付费配额。检查落在
    *引用语法文本*而非渲染结果上——三段论渲染恒产出非空第三段，渲染后已无从判别。

    """
    return render_video_unit_prompt(
        unit,
        project,
        settings,
        request_references=request_references,
    )


def _reference_limit_warning(*, provider: str, model: str | None, count: int, max_refs: int) -> dict[str, Any]:
    """参考图片超限 warning；通用路径与商品优先裁剪共用。"""
    if provider.lower() == "openai" and (model or "").lower().startswith("sora") and max_refs == 1:
        return {"key": "ref_sora_single_ref", "params": {}}
    return {
        "key": "ref_too_many_images",
        "params": {"count": count, "model": model or provider, "max_count": max_refs},
    }


async def resolve_new_unit_request_facts(
    project: dict,
    *,
    with_references: bool,
    resolver: ConfigResolver | None = None,
) -> VideoRequestFactsResult:
    """新建 unit 所落桶的读侧视频请求事实；桶判据与执行期请求投影同源（是否带参考图）。

    配置库读不出时按能力不可用返回失败：新建 unit 只是退到兼容默认时长，不代表生成可执行。
    """

    generation_type = reference_video_bucket(with_references=with_references)
    return await evaluate_video_request_facts(
        project,
        route="reference_video",
        generation_type=generation_type,
        identity=CONFIGURED_VIDEO_IDENTITY,
        resolver=resolver or ConfigResolver(async_session_factory),
    )


def default_unit_duration(request_facts: VideoRequestFactsResult, project: dict) -> int:
    """新建 unit 的默认时长（秒）：项目偏好 > 收窄后的最短档位 > 兜底。

    档位取视频请求事实收窄后的 ``allowed_durations``，使新建单元拿到的秒数落在它真正被生成时
    能申请到的档位内。项目偏好不是当前档位成员时（换模型后配置漂移）不采信，退到最短档；事实
    解析不出或时长由端点固定（没有档位可取）时退到共享规划基准，与执行层读不到
    unit 时长时的兜底值同源。
    """
    if isinstance(request_facts, VideoRequestFactsFailure) or not request_facts.allowed_durations:
        return DEFAULT_PLANNED_DURATION_SECONDS
    durations = request_facts.allowed_durations
    preferred = project.get("default_duration")
    if isinstance(preferred, int) and not isinstance(preferred, bool) and preferred in durations:
        return preferred
    return min(durations)


def _build_reference_audio_wiring(
    rendered: RenderedUnitPrompt,
    audio_paths: dict[str, Path],
    *,
    reference_audio_per_image: bool,
) -> tuple[list[Path], list[int] | None]:
    """把渲染产物的 ``audio_speakers``（+ 图号对齐下标）组装成请求字段，narration/drama 与 ad
    共用同一份组装口径。

    ``reference_audio_per_image`` 为 True 时（backend 要求音频逐段挂在具体参考素材项上），
    渲染层已按 ``VoiceRenderSettings.requires_reference_image`` 门控排除无参考图的 speaker，
    ``audio_speaker_reference_index`` 此时不应再有 None——仍按下标同时过滤两个列表而非直接
    zip 全量，是为了在渲染层与本处口径将来漂移时，两个列表始终等长同序，不会因为其中一个
    多出未过滤的 None 项而导致音频与参考图下标错位、静默绑错角色。
    """
    if reference_audio_per_image:
        paired = [
            (audio_paths[name], idx)
            for name, idx in zip(rendered.audio_speakers, rendered.audio_speaker_reference_index, strict=True)
            if idx is not None
        ]
        return [path for path, _ in paired], [idx for _, idx in paired]
    return [audio_paths[name] for name in rendered.audio_speakers], None


async def execute_reference_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    script_file: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
    claimed_provider_id: str | None = None,
    poll_timeout_seconds: int = DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS,
    stage_media_for_task: Callable[
        [Path, str, tuple[ProviderMediaInput, ...]], Awaitable[tuple[StagedProviderMedia, ...]]
    ]
    | None = None,
) -> dict[str, Any]:
    """处理一个 reference_video unit 的生成。

    resource_id 即 unit_id（E{集}U{序号}）；所有创作类型都从自包含 ``video_units`` 读取。
    """
    # Queue rows own the frozen locator. Payload remains a compatibility fallback for direct/internal callers,
    # but production dispatch passes Task.script_file explicitly so mutable payload cannot redirect execution.
    payload_script_file = payload.get("script_file")
    if script_file is None and isinstance(payload_script_file, str):
        script_file = payload_script_file
    if not isinstance(script_file, str) or not script_file:
        raise ValueError("script_file is required for reference_video task")

    # 1. 加载上下文（阻塞 IO，线程池）
    def _load():
        pm = get_project_manager()
        project = pm.load_project(project_name)
        project_path = pm.get_project_path(project_name)
        script = pm.load_script(project_name, script_file)
        script_input = resolve_usable_episode_script_input(
            project_path=project_path,
            project=project,
            script=script,
            script_filename=script_file,
        )
        units = script.get("video_units") or []
        unit = next((u for u in units if isinstance(u, dict) and u.get("unit_id") == resource_id), None)
        if unit is None:
            raise ValueError(f"unit not found: {resource_id}")
        if video_unit_replan_problems(unit):
            raise ValueError(f"unit needs replanning: {resource_id}")
        return project, project_path, script, unit, script_input

    project, project_path, script, unit, script_input = await asyncio.to_thread(_load)

    declared_references = unit_reference_declarations(project, unit)
    resolved_assets = resolve_reference_assets(project, project_path, unit)
    asset_availability = CurrentReferenceAssets(project_path, project)
    hydration = hydrate_reference_assets(declared_references, resolved_assets, asset_availability)

    # 2. 单次解析生成上下文（声明 video lane）：构造 generator 并按实际 backend 身份
    #    查能力上限与 resolution。provider 身份解析收口于 GenerationContext
    #    （docs/adr/0049）——executor 不再触碰 MediaGenerator 私有属性、不再手工重建
    #    registry provider_id。桶按本 unit 解析后的实际参考图分流（docs/adr/0054）：
    #    有参考图 → r2v；无参考图的视频单元降级 → i2v，不送入拒空参考的 r2v 桶模型。
    #    判据取解析结果而非声明，与 backend 的实际请求同源。
    execution_generation_type = reference_video_bucket(with_references=bool(hydration.available))
    execution_payload = without_reference_video_execution_identity(payload)
    request_options = ReferenceRequestOptions.from_payload(payload, legacy_duration_confirmed=True)
    ctx = await resolve_generation_context(
        project_name,
        execution_payload,
        project=project,
        user_id=user_id,
        video=VideoLaneRequest(generation_type=execution_generation_type, route="reference_video"),
        audio=AudioLaneRequest() if request_options.narration_delivery == USE_TTS else None,
    )
    generator = ctx.generator
    video = ctx.video
    actual_provider_id = video.provider_model.provider_id
    if claimed_provider_id is not None and actual_provider_id != claimed_provider_id:
        raise DispatchProviderChanged(
            claimed_provider_id=claimed_provider_id,
            actual_provider_id=actual_provider_id,
        )
    provider_name = video.backend_name
    model_name = video.backend_model

    # 3. 能力只读 lane 以实际 backend 身份（provider_id + backend.model）求得的视频请求事实：
    #    自定义模型禁用回退时也直接命中活跃 model 的能力，不会「按旧模型裁剪、按新模型生成」。
    #    求值失败由公共投影转成结构化 blocker，不制造无约束申请。引用展开、实际文件存在、商品
    #    优先裁剪、时长取档与音频冲突都由同一 projector 给出。payload 未声明请求选项时按直接
    #    入队兼容语义视为已确认；显式选项保存在 reference_request_options 中。
    lane_request_facts = video.request_facts
    if lane_request_facts is None:
        raise RuntimeError("reference video lane is missing its request facts")

    async def _lane_request_facts(generation_type: VideoGenerationType) -> VideoRequestFactsResult:
        # lane 与投影按同一份资产水合定桶；两次水合之间资产变了会落到另一个桶，而 lane 只为
        # 本桶构造了 backend，按能力不可用阻断。
        if generation_type != execution_generation_type:
            return VideoRequestFactsFailure("reference_capability_unavailable", (("capability", generation_type),))
        return lane_request_facts

    tts_in_progress = (
        await tts_task_in_progress(
            project_name=project_name,
            resource_id=resource_id,
            script_file=str(script_file),
            user_id=user_id,
        )
        if request_options.narration_delivery == USE_TTS
        else False
    )
    options = await prepare_current_reference_video_request_options(
        request_facts_lookup=_lane_request_facts,
        project=project,
        script=script,
        script_file=str(script_file),
        unit=unit,
        project_path=project_path,
        options=request_options,
        project_name=project_name,
        tts_settings_resolver=(
            ResolvedTtsSettingsResolver.from_audio_lane(ctx.audio)
            if request_options.narration_delivery == USE_TTS
            else None
        ),
        tts_in_progress=tts_in_progress,
    )
    projection = await ReferenceUnitRequestProjector(_lane_request_facts, asset_availability).project_current(
        project=project,
        script=script,
        unit=unit,
        resolved_assets=resolved_assets,
        options=options,
    )
    if projection.blocking_problems:
        raise ReferenceProjectionBlockedError(projection.blocking_problems[0])
    if projection.request_duration is None:
        raise ValueError("reference request projection has no duration tier")

    constrained_entries = list(projection.request_assets)
    formal_input_claims = (script_input.claim,)
    if task_id is None:
        formal_input_claims = (
            script_input.claim,
            *asset_availability.snapshot_selected_claims(constrained_entries),
        )
    constrained_refs = [entry.path for entry in constrained_entries]
    aspect_ratio = resolve_video_aspect_ratio(project)
    request_facts: VideoRequestFacts | None = projection.request_facts
    if request_facts is None:
        raise RuntimeError("allowed reference request is missing its request facts")
    # 未设分辨率即不下发（``docs/adr/0086``），请求、检查点与执行指纹都取这一个值。
    resolution = request_facts.resolution

    def _current_visual_basis_digest() -> str:
        return reference_video_visual_basis_digest(
            project=project,
            project_path=project_path,
            unit=unit,
            request_assets=constrained_entries,
            request_facts=request_facts,
        )

    visual_basis_digest = await asyncio.to_thread(_current_visual_basis_digest)
    effective_duration = projection.request_duration.seconds
    warnings: list[dict[str, Any]] = []
    for problem in projection.problems:
        params = problem.parameters()
        if problem.code == "reference_images_clamped":
            count = params.get("count")
            max_count = params.get("max_count")
            if not isinstance(count, int) or isinstance(count, bool):
                continue
            if not isinstance(max_count, int) or isinstance(max_count, bool):
                continue
            warnings.append(
                _reference_limit_warning(
                    provider=provider_name,
                    model=model_name,
                    count=count,
                    max_refs=max_count,
                )
            )
    duration_warning = projection.request_duration.warning(model=model_name)
    if duration_warning is not None:
        warnings.append(duration_warning)

    if request_options.narration_delivery == USE_TTS:
        narration = options.narration_preparation
        if narration is None or narration.actual_duration_seconds is None:
            raise RuntimeError("allowed TTS reference request is missing actual narration duration")
        reused = await reuse_current_video_for_tier(
            project_path=project_path,
            versions=generator.versions,
            item=unit,
            resource_type="reference_videos",
            resource_id=resource_id,
            request_duration_seconds=effective_duration,
            minimum_actual_duration_seconds=narration.actual_duration_seconds,
            visual_basis_digest=visual_basis_digest,
            revalidate_visual_basis_digest=_current_visual_basis_digest,
            warnings=warnings,
        )
        if reused is not None:
            return reused

    # 4. 所有创作类型共用三段论渲染。解析条目同时携带请求路径与逻辑主体；商品的一条逻辑
    #    引用可展开成多张图片，裁剪后直接按条目传给渲染，保证 `图片N` 的 1-based 索引与
    #    调用通道实际收到的 reference_images 一一对应。商品高保真尾注也只点名仍实际发图的商品。
    #    prompt 始终从执行期新读取的剧本重组（脚本可变 + 队列 dedup 不看 payload，
    #    用入队快照会丢失入队后对视频单元正文的编辑）；prompt 只在入队边界用于结构校验，
    #    不写进任务 payload。
    #    参考音频路径先解析再渲染：渲染层按「确实可用」判定绑定，`@音频N` 的编号与随请求
    #    发出的段数因此严格等长（字段指向已删文件时不会留下指向不存在段的编号）。
    audio_paths = await asyncio.to_thread(resolve_reference_audio_paths, project, project_path)
    voice_settings = VoiceRenderSettings(
        voice_consistency=request_facts.voice_consistency,
        requested_generate_audio=request_facts.requested_generate_audio,
        max_reference_audio=request_facts.max_reference_audio_count,
        model_id=model_name,
        audio_ready=audio_paths,
        requires_reference_image=request_facts.reference_audio_per_image,
    )
    rendered = _render_unit_prompt(
        unit,
        project,
        voice_settings,
        request_references=[entry.reference for entry in constrained_entries],
    )
    rendered_prompt = rendered.prompt
    reference_audio_files, reference_audio_targets = _build_reference_audio_wiring(
        rendered, audio_paths, reference_audio_per_image=voice_settings.requires_reference_image
    )
    # 解析派生的降级提示与生成结果同屏可见：与解析预览面板同一批 {key, params} 条目。
    warnings = [*warnings, *rendered.warnings]

    # 5. Worker submissions use immutable task-local inputs. The TTS narration artifact is deliberately absent:
    # it controls admission/duration but is not attached to VideoGenerationRequest.
    provider_refs = constrained_refs
    provider_audio = reference_audio_files or None
    staged_media: tuple[StagedProviderMedia, ...] = ()
    checkpoint_hook: Callable[[], Awaitable[Mapping[str, object] | None]] | None = None
    if task_id is not None:
        artifact_episode = script_input.episode
        artifact_speech_preparation = admit_script_unit("video_units", unit).preparation
        artifact_duration_basis = build_video_duration_basis(effective_duration)
        image_inputs = tuple(
            ProviderMediaInput(
                path=entry.path,
                role="reference_image",
                logical_type=entry.reference.type,
                logical_name=str(entry.reference.name),
                kind=entry.kind,
            )
            for entry in constrained_entries
        )
        if reference_audio_targets is None:
            audio_names = rendered.audio_speakers
            audio_target_values: list[int | None] = [None] * len(reference_audio_files)
        else:
            audio_names = [
                name
                for name, target in zip(
                    rendered.audio_speakers,
                    rendered.audio_speaker_reference_index,
                    strict=True,
                )
                if target is not None
            ]
            audio_target_values = list(reference_audio_targets)
        audio_inputs = tuple(
            ProviderMediaInput(
                path=path,
                role="reference_audio",
                logical_type="speaker",
                logical_name=name,
                kind="voice_reference",
                target_index=target,
            )
            for name, path, target in zip(audio_names, reference_audio_files, audio_target_values, strict=True)
        )
        current_narration = options.narration_preparation
        narration_facts = NarrationExecutionFacts(
            delivery=options.narration_delivery,
            tts_status=(current_narration.tts_status.value if current_narration is not None else "not_applicable"),
            artifact_path=(current_narration.artifact_path if current_narration is not None else ""),
            basis_digest=(current_narration.basis_digest if current_narration is not None else None),
            actual_duration_seconds=(
                current_narration.actual_duration_seconds if current_narration is not None else None
            ),
        )
        audio_targets_tuple = tuple(reference_audio_targets) if reference_audio_targets is not None else None
        stage = stage_media_for_task or _stage_provider_media_for_task
        staged_media = await stage(project_path, task_id, image_inputs + audio_inputs)
        try:
            staged_reference_digests = {
                media.source_locator: media.sha256 for media in staged_media if media.role == "reference_image"
            }
            formal_input_claims = (
                script_input.claim,
                *asset_availability.snapshot_selected_claims(
                    constrained_entries,
                    staged_content_digests=staged_reference_digests,
                ),
            )
            provider_refs = [
                safe_join(project_path, media.staged_locator, require_file=True)
                for media in staged_media
                if media.role == "reference_image"
            ]
            staged_audio_paths = [
                safe_join(project_path, media.staged_locator, require_file=True)
                for media in staged_media
                if media.role == "reference_audio"
            ]
            provider_audio = staged_audio_paths or None
            visual_basis_digest = await asyncio.to_thread(
                materialized_reference_video_visual_basis_digest,
                rendered_prompt=rendered_prompt,
                aspect_ratio=aspect_ratio,
                reference_images=provider_refs,
                request_assets=constrained_entries,
                reference_audio_files=staged_audio_paths,
                reference_audio_speakers=audio_names,
                reference_audio_targets=reference_audio_targets,
                request_facts=request_facts,
            )
            staged_request_assets = tuple(
                replace(entry, path=path) for entry, path in zip(constrained_entries, provider_refs, strict=True)
            )
            artifact_visual_basis = await asyncio.to_thread(
                lambda: build_reference_video_artifact_visual_basis(
                    unit=unit,
                    request_assets=staged_request_assets,
                    style=project.get("style"),
                    style_description=project_basis_style_description(project),
                    aspect_ratio=aspect_ratio,
                )
            )
            artifact_speech = await asyncio.to_thread(
                freeze_video_speech_facts,
                artifact_speech_preparation,
                characters=project.get("characters"),
                include_voice_styles=not voice_settings.is_silent,
                reference_audio_paths=dict(zip(audio_names, staged_audio_paths, strict=True)),
            )
            artifact_video_basis = compose_video_artifact_basis(
                visual=artifact_visual_basis,
                speech=artifact_speech.basis,
                duration=artifact_duration_basis,
            )
            # 付费档位并入档位集，与分镜路线同口径：时长由端点固定时档位集是空的，而产物时效事实
            # 要求档位集非空且含付费档，这里的唯一档位就是那次原样透传的秒数。
            artifact_duration_tiers = tuple(sorted({effective_duration, *request_facts.allowed_durations}))

            def _build_checkpoint() -> ReferenceSubmissionCheckpoint:
                artifact_currency = VideoArtifactCurrencyFacts(
                    episode=artifact_episode,
                    request_duration_seconds=effective_duration,
                    visual_basis=artifact_visual_basis,
                    speech_basis=artifact_speech.basis,
                    duration_basis=artifact_duration_basis,
                    video_basis=artifact_video_basis,
                    voice_style_speakers=artifact_speech.voice_style_speakers,
                    duration_tiers=artifact_duration_tiers,
                    reference_image_limit=request_facts.max_reference_images,
                    parent_version=generator.versions.get_current_version("reference_videos", resource_id),
                )
                return ReferenceSubmissionCheckpoint.create(
                    task_id=task_id,
                    project_name=project_name,
                    script_file=script_file,
                    unit_id=resource_id,
                    generation_type=execution_generation_type,
                    provider_id=video.provider_model.provider_id,
                    provider_model_id=video.provider_model.model_id,
                    backend_model_id=video.backend_model,
                    endpoint_guard=video.endpoint,
                    prompt=rendered_prompt,
                    duration_seconds=effective_duration,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    generate_audio=request_facts.requested_generate_audio,
                    service_tier="default",
                    seed=None,
                    visual_basis_digest=visual_basis_digest,
                    artifact_currency=artifact_currency,
                    narration=narration_facts,
                    media=staged_media,
                    reference_audio_targets=audio_targets_tuple,
                )

            async def _checkpoint_before_submit() -> Mapping[str, object]:
                await asyncio.to_thread(
                    assert_current_artifact_input_claims_usable,
                    project_path,
                    formal_input_claims,
                )
                checkpoint = _build_checkpoint()
                await get_generation_queue().persist_execution_checkpoint(
                    task_id,
                    checkpoint.to_json(),
                    checkpoint.provider_id,
                )
                return checkpoint_version_metadata(checkpoint)

            checkpoint_hook = _checkpoint_before_submit
        except BaseException:
            await asyncio.to_thread(
                cleanup_staged_provider_media,
                project_path,
                task_id,
            )
            raise

    artifact_committer = (
        VideoArtifactCommitter(
            project_manager=get_project_manager(),
            project_name=project_name,
            project_path=project_path,
            versions=generator.versions,
            resource_type="reference_videos",
            resource_id=resource_id,
            prompt=rendered_prompt,
        )
        if task_id is not None
        else None
    )
    try:
        await asyncio.to_thread(
            assert_current_artifact_input_claims_usable,
            project_path,
            formal_input_claims,
        )
        # MediaGenerator compresses only transient derivatives of the immutable staged images. A 413 retry keeps
        # the same high-level media identities and cannot rewrite the once-only checkpoint.
        output_path, version, _, video_uri = await generator.generate_video_async(
            prompt=rendered_prompt,
            resource_type="reference_videos",
            resource_id=resource_id,
            reference_images=provider_refs,
            reference_audio_files=provider_audio,
            reference_audio_targets=reference_audio_targets,
            aspect_ratio=aspect_ratio,
            duration_seconds=effective_duration,
            resolution=resolution,
            task_id=task_id,
            before_submit=checkpoint_hook,
            formal_output=task_id is not None,
            before_formal_commit=artifact_committer.prepare_selection if artifact_committer is not None else None,
            commit_formal_output=artifact_committer,
            visual_basis_digest=visual_basis_digest,
            generate_audio=request_facts.requested_generate_audio,
            poll_timeout_seconds=poll_timeout_seconds,
            warnings=warnings,
        )

        async def _finalize() -> dict[str, Any]:
            return await finalize_reference_video_unit(
                project_name=project_name,
                script_file=script_file,
                project_path=project_path,
                resource_id=resource_id,
                output_path=output_path,
                version=version,
                video_uri=video_uri,
                versions=generator.versions,
                warnings=warnings,
            )

        return await complete_video_artifact_commit(
            committer=artifact_committer,
            versions=generator.versions,
            resource_type="reference_videos",
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


def apply_unit_video_assets(
    script: dict,
    resource_id: str,
    *,
    video_uri: str | None,
    thumb_rel: str | None,
    generated_at: str | None = None,
) -> str | None:
    """在剧本 dict 上写回 unit.generated_assets（video_clip / video_uri / video_thumbnail / status）。

    生成 finalize 与版本还原共用，保证两条路径写出的字段口径一致。所有创作类型的 unit
    都位于 ``video_units``。新结果不含 video_uri / 缩略图时清空旧值，避免指向过期 URI /
    已删除文件。写回失败必须让调用方可见、finalize 不能在剧本未更新时静默成功，
    且两种失败要可区分：unit 不存在抛 KeyError（还原侧跨集同步把它当正常跳过），
    unit 列表结构损坏抛 ScriptEditError（还原侧按脏脚本 warning 降级）。

    ``generated_at`` 缺省戳当前时间（生成 finalize）；版本还原传入被还原版本的入库时间，
    使「片段是否早于当前参考音频设置」按还原回来的旧内容成立，而不是被还原动作洗成最新。

    迁移保留下来的旧 ``source_signature`` 是惰性历史数据，本函数不读取、比较或新增它。
    """
    units = script.get("video_units")
    if not isinstance(units, list):
        raise ScriptEditError("video_units 必须是 list", key="script_edit_unit_lists_invalid")
    for u in units:
        if not isinstance(u, dict) or u.get("unit_id") != resource_id:
            continue
        ga = u.setdefault("generated_assets", {})
        if not isinstance(ga, dict):
            raise ScriptEditError("generated_assets 必须是 dict", key="script_edit_generated_assets_invalid")
        ga["video_clip"] = f"reference_videos/{resource_id}.mp4"
        ga["video_generated_at"] = generated_at or datetime.now(UTC).isoformat()
        if video_uri:
            ga["video_uri"] = video_uri
        else:
            ga.pop("video_uri", None)
        if thumb_rel:
            ga["video_thumbnail"] = thumb_rel
        else:
            ga.pop("video_thumbnail", None)
        ga["status"] = "completed"
        return None
    raise KeyError(resource_id)


async def finalize_reference_video_unit(
    *,
    project_name: str,
    script_file: str,
    project_path: Path,
    resource_id: str,
    output_path: Path,
    version: int,
    video_uri: str | None,
    versions: VersionManager,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normal + resume + 上传共用：抽缩略图、写 unit.generated_assets、返回 result dict。

    迁移保留的旧来源签名不再参与 finalize。
    """
    warnings = warnings if warnings is not None else []

    thumb_dir = project_path / "reference_videos" / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{resource_id}.jpg"
    if await extract_video_thumbnail(output_path, thumb_path):
        thumb_rel: str | None = f"reference_videos/thumbnails/{resource_id}.jpg"
    else:
        thumb_path.unlink(missing_ok=True)
        thumb_rel = None

    def _update_unit_assets() -> str | None:
        pm = get_project_manager()
        # 资产回写热路径：只动 unit.generated_assets，结构不可能因此变坏，豁免结构校验。
        with pm.locked_script(project_name, script_file, validate=False) as script:
            return apply_unit_video_assets(script, resource_id, video_uri=video_uri, thumb_rel=thumb_rel)

    await asyncio.to_thread(_update_unit_assets)

    def _latest_created_at() -> str | None:
        # 与上面的档案补写读同一个 versions.json，同样在产物与剧本落盘之后：文件不可读时
        # 让结果少一个时间戳，而不是把已成功的付费生成报成失败——只吞掉护栏才成立。
        try:
            history = versions.get_versions("reference_videos", resource_id) or {}
        except Exception:
            logger.warning("读取版本记录取入库时间失败 resource_id=%s", resource_id, exc_info=True)
            return None
        records = history.get("versions") or []
        if not records:
            return None
        return records[-1].get("created_at")

    created_at = await asyncio.to_thread(_latest_created_at)

    return {
        "version": version,
        "file_path": f"reference_videos/{resource_id}.mp4",
        "created_at": created_at,
        "resource_type": "reference_videos",
        "resource_id": resource_id,
        "video_uri": video_uri,
        "warnings": warnings,
    }
