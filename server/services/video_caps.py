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


async def project_video_caps(project: dict, *, degraded_to: str) -> dict:
    """项目视频后端的 model 粒度能力；解析失败返回空 dict，由调用方各自降级。

    ``degraded_to`` 只用于日志，说明这次解析失败会让调用方退化成什么行为。
    """
    try:
        resolver = ConfigResolver(async_session_factory)
        return await resolver.video_capabilities_for_project(project)
    except (ValueError, SQLAlchemyError) as exc:
        logger.info("无法解析 video_capabilities，%s：%s", degraded_to, exc)
        return {}


async def resolve_project_voice_consistency(project: dict) -> VoiceConsistency:
    """项目当前视频后端的声音一致性档位，供 drama Voice_Profiles 注入前的 C 类（真无声）判定。

    解析失败按既有「无信号不判定为真无声」口径退化为 ``soft``（同 ``derive_voice_consistency``
    对自定义供应商缺失 ``generate_audio`` 声明时的处理）。
    """
    caps = await project_video_caps(project, degraded_to="Voice_Profiles 按 soft 兜底注入")
    return caps.get("voice_consistency") or "soft"
