"""项目级视频能力解析的共享出口。

按「项目当前配置的视频后端」解析 model 粒度能力，供入队前的预检使用（时长取档、
Voice_Profiles 注入判定）。入队时机尚未 resolve 任务实际的 provider/model——该解析
延后到 worker 执行时（见 ADR-0001），执行层请改用
``server.services.tasks.generation_context.resolve_generation_context`` 的精确结果。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from lib.config.resolver import (
    ConfigResolver,
    VideoGenerationType,
    caps_generation_mode,
    duration_endpoint_fixed_reason,
    video_bucket_for_generation_mode,
)
from lib.db import async_session_factory
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    audio_switch_conflict,
    evaluate_video_request_facts,
)
from lib.project.project_manager import ProjectManager
from lib.script.reference_video.request_projection import (
    ReferenceRequestFactsLookup,
    configured_reference_request_facts,
)
from lib.script.reference_video.unit_capabilities import (
    evaluate_reference_unit_capabilities,
    reference_unit_capability_payloads,
)
from lib.script.reference_video.voice_settings import VoiceRenderSettings

logger = logging.getLogger(__name__)


async def storyboard_request_facts(
    project: dict, config_resolver: ConfigResolver | None = None
) -> VideoRequestFacts | VideoRequestFactsFailure:
    """分镜路线读侧的视频请求事实：按项目生成模式定桶，以当前配置解析出的执行模型为身份。

    剧本规划与拆分工具读这一处取可选时长，与分镜预检、执行同一份收窄结果。
    """
    return await evaluate_video_request_facts(
        project,
        route="storyboard",
        generation_type=video_bucket_for_generation_mode(caps_generation_mode(project)),
        identity=CONFIGURED_VIDEO_IDENTITY,
        resolver=config_resolver or ConfigResolver(async_session_factory),
    )


def reference_request_facts_lookup(
    project: dict, config_resolver: ConfigResolver | None = None
) -> ReferenceRequestFactsLookup:
    """读侧按桶的视频请求事实查找；同一次请求内共用一份，每个桶至多求值一次。"""
    return configured_reference_request_facts(project, config_resolver or ConfigResolver(async_session_factory))


async def reference_unit_capabilities(
    project: dict,
    project_path: Path,
    units: Iterable[dict],
    *,
    request_facts: ReferenceRequestFactsLookup,
) -> dict[str, dict[str, object]]:
    """按 ``unit_id`` 索引的逐单元定桶结论与所落桶档位（Web 与 Agent 同一份）。"""
    return reference_unit_capability_payloads(
        await evaluate_reference_unit_capabilities(project, project_path, units, request_facts=request_facts)
    )


def reference_script_units(projects: ProjectManager, project_name: str, project: dict) -> list[dict]:
    """参考生视频项目全部已登记剧本的视频单元；非参考路线没有视频单元。

    剧本文件缺失的分集跳过：那是分集尚未产出剧本，不是能力问题。
    """
    if project.get("generation_mode") != "reference_video":
        return []
    units: list[dict] = []
    for episode in project.get("episodes") or []:
        script_file = episode.get("script_file") if isinstance(episode, dict) else None
        if not script_file:
            continue
        try:
            script = projects.load_script(project_name, script_file)
        except FileNotFoundError:
            continue
        units.extend(unit for unit in script.get("video_units") or [] if isinstance(unit, dict))
    return units


def video_facts_problem(failure: VideoRequestFactsFailure) -> dict:
    return failure.problem_payload()


def facts_duration_endpoint_fixed(result: VideoRequestFacts | VideoRequestFactsFailure) -> bool:
    """该桶的时长是否由端点固定；求值失败时为 False（未知不谎报）。"""
    return isinstance(result, VideoRequestFacts) and result.duration_endpoint_fixed


async def annotate_reference_no_image_caps(
    payload: dict, project: dict, *, config_resolver: ConfigResolver
) -> VideoRequestFacts | VideoRequestFactsFailure | None:
    """Attach i2v request facts to an r2v capability response."""
    if project.get("generation_mode") != "reference_video":
        return None
    result = await evaluate_video_request_facts(
        project,
        route="reference_video",
        generation_type="i2v",
        identity=CONFIGURED_VIDEO_IDENTITY,
        resolver=config_resolver,
    )
    constraints = payload["duration_constraints"]
    constraints["allowed_without_reference_images"] = (
        list(result.allowed_durations) if isinstance(result, VideoRequestFacts) else None
    )
    constraints["excluded_without_reference_images"] = (
        dict(result.excluded_durations) if isinstance(result, VideoRequestFacts) else None
    )
    constraints["without_reference_problem"] = (
        None if isinstance(result, VideoRequestFacts) else video_facts_problem(result)
    )
    fixed = facts_duration_endpoint_fixed(result)
    constraints["without_reference_duration_endpoint_fixed"] = fixed
    constraints["without_reference_duration_endpoint_fixed_reason"] = duration_endpoint_fixed_reason(fixed)
    return result


async def annotate_reference_unit_tiers(
    payload: dict,
    project: dict,
    *,
    config_resolver: ConfigResolver | None,
    projects: ProjectManager,
    project_name: str,
) -> None:
    """Add effective duration tiers only for episode reference-video units.

    ``supported_durations`` remains the model-declared full set. The annotation
    gives script authors the narrower execution tiers for units with and without
    references, plus ``units``: the server-side bucket, tiers and reference split
    of every formal unit, keyed by ``unit_id``; ad projects do not use this
    duration enumeration.
    """
    if payload.get("generation_mode") != "reference_video" or payload.get("content_mode") == "ad":
        return
    durations = [int(d) for d in payload.get("supported_durations") or []]
    if not durations and not payload.get("duration_endpoint_fixed"):
        return
    request_facts = reference_request_facts_lookup(project, config_resolver)
    with_ref_facts = await request_facts("r2v")
    without_ref_facts = await request_facts("i2v")
    units = await asyncio.to_thread(reference_script_units, projects, project_name, project)
    unit_capabilities = await reference_unit_capabilities(
        project,
        projects.get_project_path(project_name),
        units,
        request_facts=request_facts,
    )
    with_refs_fixed = facts_duration_endpoint_fixed(with_ref_facts)
    without_refs_fixed = facts_duration_endpoint_fixed(without_ref_facts)
    payload["reference_unit_durations"] = {
        "with_references": (
            list(with_ref_facts.allowed_durations) if isinstance(with_ref_facts, VideoRequestFacts) else []
        ),
        "with_references_endpoint_fixed": with_refs_fixed,
        "with_references_endpoint_fixed_reason": duration_endpoint_fixed_reason(with_refs_fixed),
        "without_references": (
            list(without_ref_facts.allowed_durations) if isinstance(without_ref_facts, VideoRequestFacts) else None
        ),
        "without_references_endpoint_fixed": without_refs_fixed,
        "without_references_endpoint_fixed_reason": duration_endpoint_fixed_reason(without_refs_fixed),
        "excluded": (
            dict(without_ref_facts.excluded_durations) if isinstance(without_ref_facts, VideoRequestFacts) else None
        ),
        "problem": None if isinstance(without_ref_facts, VideoRequestFacts) else video_facts_problem(without_ref_facts),
        "units": unit_capabilities,
    }
    # The Agent receives one no-reference channel, including failures and exclusion reasons.
    payload.get("duration_constraints", {}).pop("allowed_without_reference_images", None)


async def project_video_caps(
    project: dict,
    *,
    degraded_to: str,
    generation_type: VideoGenerationType | None = None,
) -> dict:
    """项目视频后端的 model 粒度能力；解析失败返回部分 dict（可能仅含 ``requested_generate_audio``），
    由调用方各自降级。

    ``degraded_to`` 只用于日志，说明这次解析失败会让调用方退化成什么行为。
    ``generation_type`` 未给定时按项目生成模式定桶；给定时按指定桶解析（参考生视频内无参考图视频单元
    按 i2v 桶取档 / 计价的读侧）。
    ``requested_generate_audio`` 独立于能力接口解析（见下方实现注释），双重失败时该键为 ``False``。
    """
    resolver = ConfigResolver(async_session_factory)
    try:
        return await resolver.video_capabilities_for_project(project, generation_type=generation_type)
    except (ValueError, SQLAlchemyError) as exc:
        logger.info("无法解析 video_capabilities，%s：%s", degraded_to, exc)
        caps: dict = {}
        # requested_generate_audio 不依赖能力接口，独立解析：能力解析失败不能连带把用户的
        # 无声意图丢回默认值 True，否则预览会漏发 ref_warn_silent_episode、与执行层脱节。
        try:
            caps["requested_generate_audio"] = await resolver.video_generate_audio_for_project(project)
        except (ValueError, SQLAlchemyError) as inner_exc:
            logger.info("video_generate_audio 独立解析也失败，%s：%s", degraded_to, inner_exc)
            caps["requested_generate_audio"] = False
        return caps


async def resolve_audio_switch_conflict(project: dict, generation_type: VideoGenerationType) -> tuple[str, str] | None:
    """项目的「关闭音频」意图是否落在一个收不到音轨开关的模型上；冲突时返回 ``(provider, model)``。

    成片恒有声（音轨形态 ``always_on``）的模型请求里没有音轨开关可下发，关闭意图无法抵达
    供应商，却会让编排层按无声路径裁掉全部音色约束——用户拿到的是失去音色约束的有声成片。
    视频生成的各个提交入口据此在入队前拒绝，WebUI 与 Agent 两条路径共用这一份判据。

    判据读指定桶的视频请求事实。解析失败仍放行，由其他能力预检处理。
    """
    # 模块级绑定在导入时就固化了 factory；这里延迟到调用时从 lib.db 取，测试才能替换它。
    from lib.db import async_session_factory

    resolver = ConfigResolver(async_session_factory)
    try:
        result = await evaluate_video_request_facts(
            project,
            route="reference_video" if project.get("generation_mode") == "reference_video" else "storyboard",
            generation_type=generation_type,
            identity=CONFIGURED_VIDEO_IDENTITY,
            resolver=resolver,
        )
        if isinstance(result, VideoRequestFactsFailure) or audio_switch_conflict(result) is None:
            return None
    except (ValueError, SQLAlchemyError):
        return None
    return result.provider_id, result.model_id


async def assert_audio_switch_supported(
    project: dict,
    generation_type: VideoGenerationType,
    *,
    request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None,
) -> None:
    """Agent 视频入队前的音频开关预检，冲突时抛 ``ValueError``。

    与 WebUI 入口的 ``server.routers._validators.require_audio_switch_supported`` 判据同源
    （:func:`resolve_audio_switch_conflict`），差别只在出口：这里的消息面向 Agent 转述，不走
    Translator。
    """
    if request_facts is None:
        conflict = await resolve_audio_switch_conflict(project, generation_type)
    elif isinstance(request_facts, VideoRequestFacts) and audio_switch_conflict(request_facts) is not None:
        conflict = request_facts.provider_id, request_facts.model_id
    else:
        conflict = None
    if conflict is None:
        return
    provider_id, model_id = conflict
    raise ValueError(
        f"{provider_id}/{model_id} 的成片恒有声，无法关闭音频；"
        "请让用户在设置中把音频开关改回开启后重试（当前配置会让声音照常出现，但音色约束被裁掉）"
    )


async def resolve_project_is_silent(project: dict) -> bool:
    """这一集是否听不到声音，供 drama Voice_Profiles 注入前的判定。

    两条无声路径（模型不产音的 C 类档位、本集关闭音频）在产品口径上同形，判据取自
    ``VoiceRenderSettings.is_silent``，与参考生视频渲染层、执行层
    ``server.services.tasks.generation_context.VideoLaneResult.is_silent`` 同源。

    能力解析失败时档位按「无信号不判定为真无声」退化为 ``soft``，而无声开关由
    ``project_video_caps`` 独立解析（双重失败才落 ``False``）——即解析全线失败时按无声处理，
    宁可少注入声音风格也不把用户已关闭的音频当成有声。
    """
    caps = await project_video_caps(project, degraded_to="Voice_Profiles 按无声兜底不注入")
    return VoiceRenderSettings.from_caps(caps).is_silent
