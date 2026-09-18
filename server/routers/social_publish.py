"""成片社交分发路由：选号、投递、查进度。

投递挂在成片演示的资源路径下（``/presentations/{resource_type}/{resource_id}/publish``），
与预览、打包下载同址：三者说的都是「这一版成片」，换成独立的顶层路径会让调用方再自行拼一次
选版参数，选版语义从此有两处真相。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from lib.api_errors import ApiError
from lib.config.service import ConfigService
from lib.project_manager import get_project_manager
from lib.social_publish import VIDEO_PLATFORMS
from server.dependencies import get_config_service, require_project_migration_ok
from server.services.presentation_read_model import PresentationUnavailableError
from server.services.social_publish import SocialPublishService, UploadPostCredentials, load_credentials

router = APIRouter()

ResourceType = Literal["videos", "reference_videos"]
Variant = Literal["post_production", "use_tts"]


def get_social_publish_service() -> SocialPublishService:
    return SocialPublishService(get_project_manager())


SocialPublishServiceDep = Annotated[SocialPublishService, Depends(get_social_publish_service)]
ConfigServiceDep = Annotated[ConfigService, Depends(get_config_service)]


async def get_credentials(config: ConfigServiceDep) -> UploadPostCredentials:
    return await load_credentials(config)


CredentialsDep = Annotated[UploadPostCredentials, Depends(get_credentials)]


class PublishRequest(BaseModel):
    """一次投递的请求体。选版参数与预览/打包下载同名同义。"""

    platforms: list[str] = Field(min_length=1)
    title: str
    variant: Variant = "post_production"
    video_version: int | None = Field(None, ge=1)
    audio_version: int | None = Field(None, ge=1)
    description: str | None = None
    #: ISO-8601 定时发布时间；留空即刻投递。
    scheduled_date: str | None = None
    #: IANA 时区名，配合 scheduled_date 解释其挂钟时间；留空按 UTC。
    timezone: str | None = None


@router.get("/social/publish/profiles")
async def list_publish_profiles(
    service: SocialPublishServiceDep,
    credentials: CredentialsDep,
) -> dict[str, object]:
    """列出可投递的档案与其已连接账号。"""
    profiles = await service.list_profiles(credentials)
    return {
        "active_profile": credentials.profile,
        "video_platforms": list(VIDEO_PLATFORMS),
        "profiles": [profile.to_dict() for profile in profiles],
    }


@router.post(
    "/projects/{project_name}/presentations/{resource_type}/{resource_id}/publish",
    status_code=202,
    # 逐路由挂载而非整个 router：本 router 的档案与进度两条路由没有 project_name 路径参数，
    # 而该守卫在缺参时按「挂错了」fail-fast。
    dependencies=[Depends(require_project_migration_ok)],
)
async def publish_presentation(
    project_name: str,
    resource_type: ResourceType,
    resource_id: str,
    body: PublishRequest,
    service: SocialPublishServiceDep,
    credentials: CredentialsDep,
) -> dict[str, object]:
    """把选中的成片投递到社交平台。回执带 request_id，进度另行轮询。"""
    try:
        submission = await service.publish_unit(
            credentials,
            project_name=project_name,
            resource_type=resource_type,
            resource_id=resource_id,
            variant=body.variant,
            platforms=tuple(body.platforms),
            title=body.title,
            video_version=body.video_version,
            audio_version=body.audio_version,
            description=body.description,
            scheduled_date=body.scheduled_date,
            timezone=body.timezone,
        )
    except PresentationUnavailableError as exc:
        # 与预览、打包下载同一回法：选中的版本不在了是客户端可修正的状态，不是服务端故障。
        # 异常消息含项目内路径，只进日志。
        raise ApiError("presentation_unavailable", status_code=422) from exc
    return submission.to_dict()


@router.get("/social/publish/status")
async def get_publish_status(
    service: SocialPublishServiceDep,
    credentials: CredentialsDep,
    request_id: str = Query(min_length=1),
) -> dict[str, object]:
    """查一次投递的聚合进度。"""
    progress = await service.fetch_progress(credentials, request_id=request_id)
    return progress.to_dict()


__all__ = ["get_credentials", "get_social_publish_service", "router"]
