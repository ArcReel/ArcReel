"""Shared project-level video capability resolution.

Resolve model-level capabilities from the project’s currently configured backend for pre-enqueue duration-tier
and Voice_Profiles checks. Actual task provider/model resolution happens later in worker execution (ADR-0001);
execution must use the exact ``server.services.tasks.generation_context.resolve_generation_context`` result."""

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
    ResolutionOverride,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    audio_switch_conflict,
    evaluate_video_request_facts,
    require_video_request_facts,
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
    """Read-side video-request facts for storyboard routes: select buckets by project mode and identity by the execution model resolved from current configuration.

    Planning/splitting tools obtain durations here, sharing narrowed results with storyboard preflight and execution."""
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
    """Read-side bucket lookup for video-request facts; share within each request, evaluating each bucket at most once."""
    return configured_reference_request_facts(project, config_resolver or ConfigResolver(async_session_factory))


async def reference_unit_capabilities(
    project: dict,
    project_path: Path,
    units: Iterable[dict],
    *,
    request_facts: ReferenceRequestFactsLookup,
) -> dict[str, dict[str, object]]:
    """Per-unit bucket decisions and effective tiers keyed by ``unit_id``, shared by Web and Agent."""
    return reference_unit_capability_payloads(
        await evaluate_reference_unit_capabilities(project, project_path, units, request_facts=request_facts)
    )


def reference_script_units(projects: ProjectManager, project_name: str, project: dict) -> list[dict]:
    """Video units from all registered scripts in reference-video projects; nonreference routes have no video units.

    Read only, without persisting legacy migrations. Skip missing scripts as episodes not yet authored rather than
    capability problems. Also skip/log unreadable or unparseable episodes so one corrupt episode cannot fail the capability query."""
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
    """Whether the endpoint fixes this bucket’s duration; False on evaluation failure, without claiming an unknown value is fixed."""
    return isinstance(result, VideoRequestFacts) and result.duration_endpoint_fixed


def duration_constraints_payload(facts: VideoRequestFacts) -> dict:
    """Capability ``duration_constraints``: request resolution, narrowed tiers and exclusion reasons from this bucket’s request facts."""
    return {
        "resolution": facts.resolution,
        "uses_reference_images": facts.generation_type == "r2v",
        "allowed": list(facts.allowed_durations),
        "excluded": dict(facts.excluded_durations),
    }


async def capability_request_facts(
    project: dict,
    *,
    generation_type: VideoGenerationType,
    config_resolver: ConfigResolver,
    resolution_override: ResolutionOverride | None = None,
) -> VideoRequestFacts:
    """Video-request facts for the queried bucket, resolving the execution model from current configuration and supplying ``duration_constraints``.

    Only settings-page/creation-wizard capability previews pass ``resolution_override``. Evaluation failure raises
    :class:`~lib.generation.video_request_facts.VideoRequestFactsError`."""
    route = (
        "reference_video"
        if generation_type == "r2v" or project.get("generation_mode") == "reference_video"
        else "storyboard"
    )
    return require_video_request_facts(
        await evaluate_video_request_facts(
            project,
            route=route,
            generation_type=generation_type,
            identity=CONFIGURED_VIDEO_IDENTITY,
            resolver=config_resolver,
            resolution_override=resolution_override,
        )
    )


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


async def project_video_caps(
    project: dict,
    *,
    degraded_to: str,
    generation_type: VideoGenerationType | None = None,
) -> dict:
    """Model-level project video capabilities; resolution failure returns a partial dict, possibly only ``requested_generate_audio``,
    for caller-specific fallback.

    ``degraded_to`` is logging-only, describing fallback behavior after failure. Omitted ``generation_type`` selects by
    project mode; supplied values resolve the specified bucket, including read-side i2v tiers/pricing for reference-video
    units without images. Resolve ``requested_generate_audio`` independently of capability queries (see comments below);
    if both resolutions fail, that key is ``False``."""
    resolver = ConfigResolver(async_session_factory)
    try:
        return await resolver.video_capabilities_for_project(project, generation_type=generation_type)
    except (ValueError, SQLAlchemyError) as exc:
        logger.info("无法解析 video_capabilities，%s：%s", degraded_to, exc)
        caps: dict = {}
        # Resolve requested_generate_audio independently; capability failure must not reset user-disabled
        # audio to default True, or previews omit ref_warn_silent_episode and diverge from execution.
        try:
            caps["requested_generate_audio"] = await resolver.video_generate_audio_for_project(project)
        except (ValueError, SQLAlchemyError) as inner_exc:
            logger.info("video_generate_audio 独立解析也失败，%s：%s", degraded_to, inner_exc)
            caps["requested_generate_audio"] = False
        return caps


class AudioSwitchConflict(ValueError):
    """Audio-disabled intent on a model whose output always has audio; the message is Agent-facing and attributes let UI render display names."""

    def __init__(self, provider_id: str, model_id: str) -> None:
        super().__init__(
            f"{provider_id}/{model_id} 的成片恒有声，无法关闭音频；"
            "请让用户在设置中把音频开关改回开启后重试（当前配置会让声音照常出现，但音色约束被裁掉）"
        )
        self.provider_id = provider_id
        self.model_id = model_id


async def resolve_audio_switch_conflict(project: dict, generation_type: VideoGenerationType) -> tuple[str, str] | None:
    """Whether audio-disabled project intent targets a model that cannot receive an audio switch; return ``(provider, model)`` on conflict.

    Always-audible models (``always_on`` audio) accept no audio switch. Disabled intent cannot reach the provider but
    orchestration would still strip voice constraints via the silent path, producing audible output without voice constraints.
    Video submission entry points reject this before enqueueing using the same predicate for WebUI and Agent.

    Read request facts for the specified bucket. Resolution failure still passes, leaving other capability checks to handle it."""
    # Module-level binding freezes the factory at import; retrieve from lib.db at call time so tests can replace it.
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
    """Agent pre-enqueue audio-switch checks; conflicts raise :class:`AudioSwitchConflict`.

    Share :func:`resolve_audio_switch_conflict` with WebUI ``server.routers._validators.require_audio_switch_supported``.
    Only the response differs: Agent-facing messages here bypass Translator."""
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
    """Whether this episode is inaudible, checked before injecting drama Voice_Profiles.

    Both silent paths, class C models without audio and episode audio disabled, share ``VoiceRenderSettings.is_silent``
    with reference-video rendering and execution’s ``server.services.tasks.generation_context.VideoLaneResult.is_silent``.

    Capability failure degrades to ``soft``, since absent evidence does not prove a no-audio model. ``project_video_caps``
    resolves the audio switch independently, falling to ``False`` only on double failure. Thus total resolution failure
    is treated as silent: prefer omitting voice style over treating user-disabled audio as audible."""
    caps = await project_video_caps(project, degraded_to="Voice_Profiles 按无声兜底不注入")
    return VoiceRenderSettings.from_caps(caps).is_silent
