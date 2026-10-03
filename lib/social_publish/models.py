"""Upload-Post 分发通道的出站/入站数据形状。

只放跨层传递的值对象：客户端把上游 JSON 收敛成这些 dataclass，服务层与路由层不再碰
原始响应体。上游按平台分别演化字段（`post_id` / `video_urn` / `container_id` …），此处
统一成 `PlatformOutcome.url` + `raw_status`，读侧不必按平台分支取值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: 上游接受视频投稿的平台标识。与 `GET /uploadposts/users` 返回的 `social_accounts` 键同名，
#: 前端据此把「已连接账号」与「可投递平台」对上；未列出的平台（reddit 只收图文）不进视频下拉。
VIDEO_PLATFORMS: tuple[str, ...] = (
    "tiktok",
    "instagram",
    "youtube",
    "linkedin",
    "facebook",
    "x",
    "threads",
    "pinterest",
    "bluesky",
    "discord",
    "telegram",
)

#: 单个平台在一次投递里的终态判定。`skipped` 是上游对「该档案没连这个平台」的回法，
#: 既不算成功也不算失败，读侧按它提示去连号而不是报错。
PlatformStatus = Literal["queued", "processing", "completed", "failed", "retryable", "skipped"]

#: 整次投递的聚合状态，取自上游 `GET /uploadposts/status` 的顶层 `status`。
SubmissionStatus = Literal["pending", "queued", "processing", "in_progress", "completed", "failed"]


@dataclass(frozen=True, slots=True)
class ConnectedAccount:
    """某档案下一个已连接的社交账号。"""

    platform: str
    display_name: str
    handle: str
    #: 上游标记该连接需要重新授权；带这个标的账号投递必失败，前端先行灰掉。
    reauth_required: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "display_name": self.display_name,
            "handle": self.handle,
            "reauth_required": self.reauth_required,
        }


@dataclass(frozen=True, slots=True)
class PublishProfile:
    """Upload-Post 的「档案」：一组已连接账号的集合，投递时用它的 username 定位。"""

    username: str
    accounts: tuple[ConnectedAccount, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "username": self.username,
            "accounts": [account.to_dict() for account in self.accounts],
        }


@dataclass(frozen=True, slots=True)
class PublishSubmission:
    """一次投递被上游受理后的回执。

    `request_id` 由本端生成并原样回传：上游文档明确，HTTP 响应丢失（超时、504）时仍能用
    它查状态，因此它是投递与后续轮询之间唯一必需的关联键。定时投递另给 `job_id`。
    """

    request_id: str
    job_id: str | None
    scheduled_date: str | None
    total_platforms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "job_id": self.job_id,
            "scheduled_date": self.scheduled_date,
            "total_platforms": self.total_platforms,
        }


@dataclass(frozen=True, slots=True)
class PlatformOutcome:
    """单平台的投递结果。"""

    platform: str
    status: PlatformStatus
    success: bool
    #: 成品贴文地址；排队中、失败、或上游该平台不返回可访问链接时为 None。
    url: str | None
    #: 上游原文的失败原因，不本地化——它描述的是社交平台的拒因，翻译会失真。
    error: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "status": self.status,
            "success": self.success,
            "url": self.url,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class PublishProgress:
    """一次投递的聚合进度。"""

    request_id: str | None
    job_id: str | None
    status: SubmissionStatus
    completed: int
    total: int
    outcomes: tuple[PlatformOutcome, ...]

    @property
    def terminal(self) -> bool:
        """轮询是否可以停：上游只有 completed / failed 两个终态。"""
        return self.status in ("completed", "failed")

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "job_id": self.job_id,
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "terminal": self.terminal,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


__all__ = [
    "VIDEO_PLATFORMS",
    "ConnectedAccount",
    "PlatformOutcome",
    "PlatformStatus",
    "PublishProfile",
    "PublishProgress",
    "PublishSubmission",
    "SubmissionStatus",
]
