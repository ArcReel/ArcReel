"""项目级视频能力解析的共享出口。

按「项目当前配置的视频后端」解析 model 粒度能力，供入队前的预检使用（时长取档、
Voice_Profiles 注入判定）。入队时机尚未 resolve 任务实际的 provider/model——该解析
延后到 worker 执行时（见 ADR-0001），执行层请改用
``server.services.generation_context.resolve_generation_context`` 的精确结果。
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from lib.config.resolver import ConfigResolver, VoiceConsistency
from lib.db import async_session_factory

logger = logging.getLogger(__name__)


async def project_video_caps(project: dict, *, degraded_to: str, episode: int | None = None) -> dict:
    """项目视频后端的 model 粒度能力；解析失败返回部分 dict（可能仅含 ``requested_generate_audio``），
    由调用方各自降级。

    ``degraded_to`` 只用于日志，说明这次解析失败会让调用方退化成什么行为。
    ``episode`` 给出集号时按该集生效 ``generation_mode`` 解析（生成模式可被单集覆盖）。
    ``requested_generate_audio`` 独立于能力接口解析（见下方实现注释），双重失败时该键为 ``False``。
    """
    resolver = ConfigResolver(async_session_factory)
    try:
        return await resolver.video_capabilities_for_project(project, episode)
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


async def resolve_project_voice_consistency(project: dict, episode: int | None = None) -> VoiceConsistency:
    """该集视频后端的声音一致性档位，供 drama Voice_Profiles 注入前的 C 类（真无声）判定。

    解析失败按既有「无信号不判定为真无声」口径退化为 ``soft``（同 ``derive_voice_consistency``
    对自定义供应商缺失 ``generate_audio`` 声明时的处理）。
    """
    caps = await project_video_caps(project, degraded_to="Voice_Profiles 按 soft 兜底注入", episode=episode)
    return caps.get("voice_consistency") or "soft"
