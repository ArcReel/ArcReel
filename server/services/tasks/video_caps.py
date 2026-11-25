"""项目级视频能力解析的共享出口。

按「项目当前配置的视频后端」解析 model 粒度能力，供入队前的预检使用（时长取档、
Voice_Profiles 注入判定）。入队时机尚未 resolve 任务实际的 provider/model——该解析
延后到 worker 执行时（见 ADR-0001），执行层请改用
``server.services.tasks.generation_context.resolve_generation_context`` 的精确结果。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

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
    ResolutionOverride,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    audio_switch_conflict,
    project_video_capabilities_with_facts,
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
from lib.video_runtime.resolution import VideoRuntime, read_video_request_facts

logger = logging.getLogger(__name__)


async def storyboard_request_facts(
    project: dict, config_resolver: ConfigResolver | None = None
) -> VideoRequestFacts | VideoRequestFactsFailure:
    """分镜路线读侧的视频请求事实：按项目生成模式定桶，消费单次解析能力快照与纯同步求值。"""
    return await read_video_request_facts(
        project,
        route="storyboard",
        generation_type=video_bucket_for_generation_mode(caps_generation_mode(project)),
        resolver=config_resolver or ConfigResolver(async_session_factory),
    )


async def resolve_project_video_capabilities_and_facts(
    project: dict | None,
    *,
    generation_type: VideoGenerationType,
    config_resolver: ConfigResolver,
    resolution_override: ResolutionOverride | None = None,
    include_planning: bool = False,
) -> tuple[dict[str, Any], VideoRequestFacts]:
    """单次解析视频能力快照并同步投影项目级能力、时长档位、尾帧档位与主桶事实。"""
    read = await VideoRuntime(config_resolver).read_bucket(project, generation_type)
    return project_video_capabilities_with_facts(
        read.snapshot,
        project,
        generation_type=generation_type,
        requested_audio=read.requested_audio,
        resolution_override=resolution_override,
        include_planning=include_planning,
    )


async def resolve_project_video_capabilities(
    project: dict | None,
    *,
    generation_type: VideoGenerationType,
    config_resolver: ConfigResolver,
    resolution_override: ResolutionOverride | None = None,
    include_planning: bool = False,
) -> dict[str, Any]:
    """单次解析视频能力快照并同步投影项目级能力、时长档位与尾帧档位。"""
    payload, _ = await resolve_project_video_capabilities_and_facts(
        project,
        generation_type=generation_type,
        config_resolver=config_resolver,
        resolution_override=resolution_override,
        include_planning=include_planning,
    )
    return payload


async def resolve_agent_video_capabilities(
    project: dict,
    *,
    config_resolver: ConfigResolver,
    projects: ProjectManager,
    project_name: str,
) -> dict[str, Any]:
    """在单次设置快照会话内投影项目视频能力并标注参考生视频分桶档位，复用已解析主桶事实。"""
    generation_type = video_bucket_for_generation_mode(caps_generation_mode(project))
    async with config_resolver.session(snapshot_settings=True) as resolver:
        payload, facts = await resolve_project_video_capabilities_and_facts(
            project,
            generation_type=generation_type,
            config_resolver=resolver,
        )
        await annotate_reference_unit_tiers(
            payload,
            project,
            config_resolver=resolver,
            projects=projects,
            project_name=project_name,
            initial_facts={generation_type: facts},
        )
    return payload


def reference_request_facts_lookup(
    project: dict,
    config_resolver: ConfigResolver | None = None,
    *,
    initial_facts: Mapping[VideoGenerationType, VideoRequestFacts | VideoRequestFactsFailure] | None = None,
) -> ReferenceRequestFactsLookup:
    """读侧按桶的视频请求事实查找；同一次请求内共用一份，每个桶至多求值一次。"""
    return configured_reference_request_facts(
        project,
        config_resolver or ConfigResolver(async_session_factory),
        initial_facts=initial_facts,
    )


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

    只读取、不回写存量迁移。剧本文件缺失的分集跳过：那是分集尚未产出剧本，不是能力问题；读不出
    或解析不了的分集同样跳过并记日志，单集损坏不拖垮整份能力查询。
    """
    if project.get("generation_mode") != "reference_video":
        return []
    units: list[dict] = []
    for episode in project.get("episodes") or []:
        script_file = episode.get("script_file") if isinstance(episode, dict) else None
        if not script_file:
            continue
        try:
            script = projects.load_script_readonly(project_name, script_file)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            logger.warning("能力标注跳过无法读取的剧本 %s/%s：%s", project_name, script_file, exc)
            continue
        units.extend(unit for unit in script.get("video_units") or [] if isinstance(unit, dict))
    return units


def video_facts_problem(failure: VideoRequestFactsFailure) -> dict:
    return failure.problem_payload()


def facts_duration_endpoint_fixed(result: VideoRequestFacts | VideoRequestFactsFailure) -> bool:
    """该桶的时长是否由端点固定；求值失败时为 False（未知不谎报）。"""
    return isinstance(result, VideoRequestFacts) and result.duration_endpoint_fixed


async def annotate_reference_unit_tiers(
    payload: dict,
    project: dict,
    *,
    config_resolver: ConfigResolver | None,
    projects: ProjectManager,
    project_name: str,
    initial_facts: Mapping[VideoGenerationType, VideoRequestFacts | VideoRequestFactsFailure] | None = None,
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
    request_facts = reference_request_facts_lookup(project, config_resolver, initial_facts=initial_facts)
    with_ref_facts = await request_facts("r2v")
    without_ref_facts = await request_facts("i2v")
    project_path, units = await asyncio.to_thread(
        lambda: (projects.get_project_path(project_name), reference_script_units(projects, project_name, project))
    )
    unit_capabilities = await reference_unit_capabilities(
        project,
        project_path,
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
    bucket = generation_type or video_bucket_for_generation_mode(caps_generation_mode(project))
    try:
        return await resolve_project_video_capabilities(
            project,
            generation_type=bucket,
            config_resolver=resolver,
        )
    except (ValueError, SQLAlchemyError) as exc:
        logger.info("无法解析 video_capabilities，%s：%s", degraded_to, exc)
        caps: dict = {}
        try:
            caps["requested_generate_audio"] = await resolver.video_generate_audio_for_project(project)
        except (ValueError, SQLAlchemyError) as inner_exc:
            logger.info("video_generate_audio 独立解析也失败，%s：%s", degraded_to, inner_exc)
            caps["requested_generate_audio"] = False
        return caps


class AudioSwitchConflict(ValueError):
    """关闭音频的意图落在成片恒有声的模型上；消息面向 Agent 转述，属性供界面按显示名渲染。"""

    def __init__(self, provider_id: str, model_id: str) -> None:
        super().__init__(
            f"{provider_id}/{model_id} 的成片恒有声，无法关闭音频；"
            "请让用户在设置中把音频开关改回开启后重试（当前配置会让声音照常出现，但音色约束被裁掉）"
        )
        self.provider_id = provider_id
        self.model_id = model_id


async def resolve_audio_switch_conflict(project: dict, generation_type: VideoGenerationType) -> tuple[str, str] | None:
    """项目的「关闭音频」意图是否落在一个收不到音轨开关的模型上；冲突时返回 ``(provider, model)``。

    成片恒有声（音轨形态 ``always_on``）的模型请求里没有音轨开关可下发，关闭意图无法抵达
    供应商，却会让编排层按无声路径裁掉全部音色约束——用户拿到的是失去音色约束的有声成片。
    视频生成的各个提交入口据此在入队前拒绝，WebUI 与 Agent 两条路径共用这一份判据。

    判据读指定桶的视频请求事实。解析失败仍放行，由其他能力预检处理。
    """
    from lib.db import async_session_factory

    resolver = ConfigResolver(async_session_factory)
    try:
        result = await read_video_request_facts(
            project,
            route="reference_video" if project.get("generation_mode") == "reference_video" else "storyboard",
            generation_type=generation_type,
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
    """Agent 视频入队前的音频开关预检，冲突时抛 :class:`AudioSwitchConflict`。

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
    raise AudioSwitchConflict(*conflict)


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
