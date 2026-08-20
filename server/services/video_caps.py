"""项目级视频能力解析的共享出口。

按「项目当前配置的视频后端」解析 model 粒度能力，供入队前的预检使用（时长取档、
Voice_Profiles 注入判定）。入队时机尚未 resolve 任务实际的 provider/model——该解析
延后到 worker 执行时（见 ADR-0001），执行层请改用
``server.services.generation_context.resolve_generation_context`` 的精确结果。
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from lib.config.registry import PROVIDER_REGISTRY, model_audio_always_on
from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError, VideoCapability
from lib.db import async_session_factory
from lib.reference_video.voice_settings import VoiceRenderSettings

logger = logging.getLogger(__name__)


async def project_video_caps(
    project: dict,
    *,
    degraded_to: str,
    capability: VideoCapability | None = None,
) -> dict:
    """项目视频后端的 model 粒度能力；解析失败返回部分 dict（可能仅含 ``requested_generate_audio``），
    由调用方各自降级。

    ``degraded_to`` 只用于日志，说明这次解析失败会让调用方退化成什么行为。
    ``capability`` 未给定时按项目生成模式定桶；给定时按指定桶解析（参考生视频内无参考图视频单元
    按 i2v 桶取档 / 计价的读侧）。
    ``requested_generate_audio`` 独立于能力接口解析（见下方实现注释），双重失败时该键为 ``False``。
    """
    resolver = ConfigResolver(async_session_factory)
    try:
        return await resolver.video_capabilities_for_project(project, capability=capability)
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


async def resolve_audio_switch_conflict(project: dict, capability: VideoCapability) -> tuple[str, str] | None:
    """项目的「关闭音频」意图是否落在一个收不到音轨开关的模型上；冲突时返回 ``(provider, model)``。

    成片恒有声（``model_audio_always_on``）的模型请求里没有音轨开关可下发，关闭意图无法抵达
    供应商，却会让编排层按无声路径裁掉全部音色约束——用户拿到的是失去音色约束的有声成片。
    视频生成的各个提交入口据此在入队前拒绝，WebUI 与 Agent 两条路径共用这一份判据。

    解析失败一律返回 ``None``（不把配置解析问题升级为提交期拒绝），自定义供应商与未登记模型
    没有逐模型音轨声明，无信号不收紧。两次解析都读库，故同在一个 ``try`` 内并一并接住
    ``SQLAlchemyError``——数据库故障退化成放行，与 :func:`project_video_caps` 的降级口径一致。
    """
    # 模块级绑定在导入时就固化了 factory；这里延迟到调用时从 lib.db 取，测试才能替换它。
    from lib.db import async_session_factory

    resolver = ConfigResolver(async_session_factory)
    try:
        selected = await resolver.resolve_video_backend(project, None, capability=capability)
        provider_meta = PROVIDER_REGISTRY.get(selected.provider_id)
        model_info = provider_meta.models.get(selected.model_id) if provider_meta is not None else None
        if model_info is None or not model_audio_always_on(selected.provider_id, model_info):
            return None
        if await resolver.video_generate_audio_for_project(project):
            return None
    except (VideoBucketCapabilityError, ValueError, SQLAlchemyError):
        return None
    return selected.provider_id, selected.model_id


async def assert_audio_switch_supported(project: dict, capability: VideoCapability) -> None:
    """Agent 视频入队前的音频开关预检，冲突时抛 ``ValueError``。

    与 WebUI 入口的 ``server.routers._validators.require_audio_switch_supported`` 判据同源
    （:func:`resolve_audio_switch_conflict`），差别只在出口：这里的消息面向 Agent 转述，不走
    Translator。
    """
    conflict = await resolve_audio_switch_conflict(project, capability)
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
    ``server.services.generation_context.VideoLaneResult.is_silent`` 同源。

    能力解析失败时档位按「无信号不判定为真无声」退化为 ``soft``，而无声开关由
    ``project_video_caps`` 独立解析（双重失败才落 ``False``）——即解析全线失败时按无声处理，
    宁可少注入声音风格也不把用户已关闭的音频当成有声。
    """
    caps = await project_video_caps(project, degraded_to="Voice_Profiles 按无声兜底不注入")
    return VoiceRenderSettings.from_caps(caps).is_silent
