"""项目级视频能力解析的共享出口。

按「项目当前配置的视频后端」解析 model 粒度能力，供入队前的预检使用（时长取档、
Voice_Profiles 注入判定）。入队时机尚未 resolve 任务实际的 provider/model——该解析
延后到 worker 执行时（见 ADR-0001），执行层请改用
``server.services.generation_context.resolve_generation_context`` 的精确结果。
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.reference_video.voice_settings import VoiceRenderSettings

logger = logging.getLogger(__name__)


async def project_video_caps(project: dict, *, degraded_to: str) -> dict:
    """项目视频后端的 model 粒度能力；解析失败返回部分 dict（可能仅含 ``requested_generate_audio``），
    由调用方各自降级。

    ``degraded_to`` 只用于日志，说明这次解析失败会让调用方退化成什么行为。
    ``requested_generate_audio`` 独立于能力接口解析（见下方实现注释），双重失败时该键为 ``False``。
    """
    resolver = ConfigResolver(async_session_factory)
    try:
        return await resolver.video_capabilities_for_project(project)
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


async def resolve_project_is_silent(project: dict) -> bool:
    """这一集是否听不到声音，供 drama Voice_Profiles 注入前的判定。

    两条无声路径（模型不产音的 C 类档位、本集关闭音频）在产品口径上同形，判据取自
    ``VoiceRenderSettings.is_silent``，与参考路线渲染层、执行层
    ``server.services.generation_context.VideoLaneResult.is_silent`` 同源。

    能力解析失败时档位按「无信号不判定为真无声」退化为 ``soft``，而无声开关由
    ``project_video_caps`` 独立解析（双重失败才落 ``False``）——即解析全线失败时按无声处理，
    宁可少注入声音风格也不把用户已关闭的音频当成有声。
    """
    caps = await project_video_caps(project, degraded_to="Voice_Profiles 按无声兜底不注入")
    return VoiceRenderSettings.from_caps(caps).is_silent
