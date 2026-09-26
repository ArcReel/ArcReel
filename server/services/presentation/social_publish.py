"""成片社交分发：把演示读模型选中的那一版投递到 Upload-Post。

投递的是「读模型此刻选中的成片」，与打包下载、剪映草稿导出同一口径：三条出口共享
``PresentationReadModelService`` 的选版语义，成片换版后三处看到的是同一个文件，不会出现
下载到新版、发布出去的却是旧版。

凭证（api_key / 档案名 / base_url）存在 system_settings，与 anthropic_api_key 同一套读写
路径；此处只负责取用与缺失时的拒绝，不碰持久化。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from lib.config.service import ConfigService
from lib.infra.api_errors import UnprocessableError
from lib.project.project_manager import ProjectManager
from lib.social_publish import (
    DEFAULT_BASE_URL,
    VIDEO_PLATFORMS,
    PublishProfile,
    PublishProgress,
    PublishSubmission,
    UploadPostClient,
)
from lib.social_publish.settings import SETTING_API_KEY, SETTING_BASE_URL, SETTING_PROFILE
from lib.speech.speech_artifact_provenance import RenditionVariant
from server.services.presentation.presentation_media import selected_media_path
from server.services.presentation.presentation_read_model import MaterializedPresentation, PresentationReadModelService

# 上游 external_id 的硬上限；超长直接被拒，本端先截断。
_EXTERNAL_ID_MAX = 255

# 各平台标题上限差异极大（TikTok 2200、YouTube 100…），逐平台校验会把平台策略搬进本仓并
# 随上游漂移。这里只挡住明显异常的长度，真正的上限由上游按平台裁决。
_TITLE_MAX = 2200

# 投递标识的形状。它同时是 ``Idempotency-Key``，会原样进请求头，因此只收不含空白与控制字符的
# 有界字符串——客户端自带的那一个也走这条校验。
_REQUEST_ID_RE = re.compile(r"^arcreel-[A-Za-z0-9]{8,64}$")


class UnitPresentationReader(Protocol):
    async def materialize_unit(
        self,
        *,
        project_name: str,
        resource_type: str,
        resource_id: str,
        variant: RenditionVariant,
        video_version: int | None = None,
        audio_version: int | None = None,
    ) -> MaterializedPresentation:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class UploadPostCredentials:
    """一次调用所需的全部上游凭证与寻址信息。"""

    api_key: str
    profile: str
    base_url: str


async def load_credentials(config: ConfigService) -> UploadPostCredentials:
    """读取分发凭证；未配置则拒绝，绝不静默退化成「什么也没发」。"""
    settings = await config.get_all_settings()
    api_key = settings.get(SETTING_API_KEY, "").strip()
    profile = settings.get(SETTING_PROFILE, "").strip()
    if not api_key or not profile:
        raise UnprocessableError("social_publish_not_configured")
    return UploadPostCredentials(
        api_key=api_key,
        profile=profile,
        base_url=settings.get(SETTING_BASE_URL, "").strip() or DEFAULT_BASE_URL,
    )


class SocialPublishService:
    """成片 → 社交平台的投递编排。"""

    def __init__(
        self,
        project_manager: ProjectManager,
        *,
        presentation_reader: UnitPresentationReader | None = None,
        client_factory: Callable[[UploadPostCredentials], UploadPostClient] | None = None,
    ) -> None:
        self._project_manager = project_manager
        self._reader = presentation_reader or PresentationReadModelService(project_manager)
        # 注入点留给测试：替身仍是真客户端，只换掉它底下的 httpx 客户端，出站请求因此
        # 照常序列化并被 respx 在 transport 层捕获。
        self._client_factory = client_factory or _default_client

    async def list_profiles(self, credentials: UploadPostCredentials) -> tuple[PublishProfile, ...]:
        """列出凭证下的全部档案，供前端选号与提示重新授权。"""
        return await self._client(credentials).list_profiles()

    async def publish_unit(
        self,
        credentials: UploadPostCredentials,
        *,
        project_name: str,
        resource_type: str,
        resource_id: str,
        variant: RenditionVariant,
        platforms: tuple[str, ...],
        title: str,
        video_version: int | None = None,
        audio_version: int | None = None,
        description: str | None = None,
        scheduled_date: str | None = None,
        timezone: str | None = None,
        request_id: str | None = None,
    ) -> PublishSubmission:
        """把选中的成片投递到指定平台。

        ``request_id`` 由调用方带来时原样沿用，这是重试能不重复发布的唯一依据：上传在上游
        已受理、响应却丢在半路（客户端超时、504）时，本端这一侧什么都没留下；调用方拿着同一个
        id 重试，上游按 ``Idempotency-Key`` 认出是同一次投递并返回原任务，而不是再发一遍——
        而社交平台侧无法回滚。缺省才现生成一个。
        """
        chosen = _validate_platforms(platforms)
        caption = _validate_title(title)
        submission_id = _validate_request_id(request_id)

        result = await self._reader.materialize_unit(
            project_name=project_name,
            resource_type=resource_type,
            resource_id=resource_id,
            variant=variant,
            video_version=video_version,
            audio_version=audio_version,
        )
        project_path = await asyncio.to_thread(self._project_manager.get_project_path, project_name)
        video_path = selected_media_path(project_path, result.presentation.video.media.artifact_path)

        return await self._client(credentials).publish_video(
            profile=credentials.profile,
            platforms=chosen,
            video_path=video_path,
            title=caption,
            request_id=submission_id,
            description=description or None,
            scheduled_date=scheduled_date or None,
            timezone=timezone or None,
            external_id=f"{project_name}:{resource_id}"[:_EXTERNAL_ID_MAX],
        )

    async def fetch_progress(self, credentials: UploadPostCredentials, *, request_id: str) -> PublishProgress:
        """查投递进度。"""
        return await self._client(credentials).fetch_progress(request_id=request_id)

    def _client(self, credentials: UploadPostCredentials) -> UploadPostClient:
        return self._client_factory(credentials)


def _default_client(credentials: UploadPostCredentials) -> UploadPostClient:
    return UploadPostClient(api_key=credentials.api_key, base_url=credentials.base_url)


def _validate_platforms(platforms: tuple[str, ...]) -> tuple[str, ...]:
    if not platforms:
        raise UnprocessableError("social_publish_platform_required")
    # 去重但保序：前端多选组件重复提交同一平台时，上游会把它当两次投递计费。
    unique = tuple(dict.fromkeys(platforms))
    unsupported = [platform for platform in unique if platform not in VIDEO_PLATFORMS]
    if unsupported:
        raise UnprocessableError("social_publish_platform_unsupported", platform=unsupported[0])
    return unique


def _validate_request_id(request_id: str | None) -> str:
    if request_id is None:
        return f"arcreel-{uuid.uuid4().hex}"
    if _REQUEST_ID_RE.fullmatch(request_id) is None:
        raise UnprocessableError("social_publish_request_id_invalid")
    return request_id


def _validate_title(title: str) -> str:
    caption = title.strip()
    if not caption:
        raise UnprocessableError("social_publish_title_required")
    if len(caption) > _TITLE_MAX:
        raise UnprocessableError("social_publish_title_too_long", limit=_TITLE_MAX)
    return caption


__all__ = [
    "SETTING_API_KEY",
    "SETTING_BASE_URL",
    "SETTING_PROFILE",
    "SocialPublishService",
    "UnitPresentationReader",
    "UploadPostCredentials",
    "load_credentials",
]
