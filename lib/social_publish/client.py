"""Upload-Post REST 客户端：把成片投递到社交平台的唯一出站点。

只封三件事——列档案、投视频、查进度——因为分发闭环只需要这三步：选号、投递、回报结果。

设计约束：

- **异步投递是默认**。上游同步投递超过 59s 会自行转后台，响应形状随之变化；恒定带
  ``async_upload=true`` 让形状只有一种（回执带 ``request_id``），调用方不必写两套解析。
- **``request_id`` 由本端生成**。上游文档说明响应丢失（客户端超时、504）时仍可凭它查状态；
  服务端生成则意味着回执一丢就再也对不上那次投递。
- **``Idempotency-Key`` 恒定随行**。投递超时后的重试是常态，没有这个头会把同一条成片
  发两遍，而社交平台侧无法回滚。
- 日志只打 URL 与 status，绝不打 body 与 api_key——后者会随 ``task.error_message`` 落库。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx

from lib.api_errors import BadGatewayError, ServiceUnavailableError, UnprocessableError
from lib.httpx_shared import get_http_client
from lib.social_publish.models import (
    ConnectedAccount,
    PlatformOutcome,
    PlatformStatus,
    PublishProfile,
    PublishProgress,
    PublishSubmission,
    SubmissionStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.upload-post.com/api"

# 档案与进度是小 JSON，慢于此即上游不可用。
_METADATA_TIMEOUT_S = 20.0

# 投视频的上限要覆盖「把文件体发完」，不是覆盖「平台发布完」——带 async_upload 时上游
# 收完请求体即回执，发布在后台跑。成片按分钟计、上百 MB 常见，20s 一档会在正常链路上误杀。
_UPLOAD_TIMEOUT_S = 600.0

# 上游拒因原文进 diagnostic，截断避免把整页 HTML 错误页塞进响应体。
_REASON_TRUNCATE = 300

_KNOWN_STATUSES: frozenset[str] = frozenset({"pending", "queued", "processing", "in_progress", "completed", "failed"})
_KNOWN_PLATFORM_STATUSES: frozenset[str] = frozenset(
    {"queued", "processing", "completed", "failed", "retryable", "skipped"}
)

# 上游按平台返回不同的贴文标识字段，取第一个非空的当作可访问链接；顺序即优先级，
# 真正的 URL 字段排在仅有 id 的字段之前。
_URL_FIELDS: tuple[str, ...] = ("url", "post_url", "permalink", "video_url")


@dataclass(frozen=True, slots=True)
class UploadPostClient:
    """按 api_key 绑定的一次性客户端；``http_client`` 缺省时用共享单例。"""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    http_client: httpx.AsyncClient | None = None

    @property
    def _client(self) -> httpx.AsyncClient:
        return self.http_client or get_http_client()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Apikey {self.api_key}"}

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    async def list_profiles(self) -> tuple[PublishProfile, ...]:
        """列出该 api_key 下的全部档案与其已连接账号。"""
        response = await self._request("GET", "/uploadposts/users", timeout_s=_METADATA_TIMEOUT_S)
        payload = _decode(response)
        raw_profiles = payload.get("profiles")
        if not isinstance(raw_profiles, list):
            raise BadGatewayError("social_publish_upstream_malformed")
        return tuple(_parse_profile(entry) for entry in raw_profiles if isinstance(entry, dict))

    async def publish_video(
        self,
        *,
        profile: str,
        platforms: tuple[str, ...],
        video_path: Path,
        title: str,
        request_id: str,
        description: str | None = None,
        scheduled_date: str | None = None,
        timezone: str | None = None,
        external_id: str | None = None,
    ) -> PublishSubmission:
        """投递一条成片。

        ``is_ai_generated`` 恒为真：本仓的成片全程由模型生成，各平台的 AI 披露开关都挂在
        这个跨平台别名下，交给调用方配置等于把合规声明做成可关的选项。
        """
        # httpx 把列表值编码成重复字段，正是上游 ``platform[]`` 要的形状。
        data: dict[str, str | list[str]] = {
            "user": profile,
            "title": title,
            "request_id": request_id,
            "async_upload": "true",
            "is_ai_generated": "true",
            "platform[]": list(platforms),
        }
        optional = {
            "description": description,
            "scheduled_date": scheduled_date,
            "timezone": timezone,
            "external_id": external_id,
        }
        data.update({key: value for key, value in optional.items() if value})

        with video_path.open("rb") as handle:
            response = await self._request(
                "POST",
                "/upload",
                timeout_s=_UPLOAD_TIMEOUT_S,
                data=data,
                files={"video": (video_path.name, handle, "video/mp4")},
                extra_headers={"Idempotency-Key": request_id},
            )
        payload = _decode(response)
        echoed = _text(payload.get("request_id"))
        if echoed is not None and echoed != request_id:
            # 幂等键用的是本端这一个；跟着上游改口会让后续轮询问的是另一次投递，
            # 而重试时发出去的仍是本端 id。只记一条日志，回执照旧用本端 id。
            logger.warning("Upload-Post 回传了不同的 request_id，按本端 id 记账")
        return PublishSubmission(
            request_id=request_id,
            job_id=_text(payload.get("job_id")),
            scheduled_date=_text(payload.get("scheduled_date")),
            total_platforms=_int(payload.get("total_platforms"), default=len(platforms)),
        )

    async def fetch_progress(self, *, request_id: str) -> PublishProgress:
        """查一次投递的聚合进度。"""
        response = await self._request(
            "GET",
            "/uploadposts/status",
            timeout_s=_METADATA_TIMEOUT_S,
            params={"request_id": request_id},
        )
        payload = _decode(response)
        raw_results = payload.get("results")
        results = raw_results if isinstance(raw_results, list) else []
        outcomes = tuple(_parse_outcome(entry) for entry in results if isinstance(entry, dict))
        return PublishProgress(
            # 与投递回执同一条规矩：问的是本端这个 id，回传不一致就跟着改会把进度挂到
            # 另一次投递上。这是独立的一条响应路径，投递侧的那道防护覆盖不到这里。
            request_id=request_id,
            job_id=_text(payload.get("job_id")),
            status=_parse_status(payload.get("status")),
            completed=_int(payload.get("completed"), default=0),
            total=_int(payload.get("total"), default=len(outcomes)),
            outcomes=outcomes,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout_s: float,
        params: dict[str, str] | None = None,
        data: dict[str, str | list[str]] | None = None,
        files: dict[str, tuple[str, Any, str]] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = self._url(path)
        headers = {**self._headers, **(extra_headers or {})}
        try:
            response = await self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data,
                files=files,
                timeout=timeout_s,
            )
        except httpx.HTTPError as exc:
            # str(exc) 可能含完整 URL 与查询串，只进日志。
            logger.warning("Upload-Post 请求失败: %s %s (%s)", method, path, type(exc).__name__)
            raise BadGatewayError("social_publish_upstream_unreachable") from exc

        if response.is_success:
            return response
        raise _status_error(response, method=method, path=path)


def _status_error(response: httpx.Response, *, method: str, path: str) -> Exception:
    """上游非 2xx → 领域异常。状态码决定「谁该动手」，拒因原文进 diagnostic。"""
    reason = _upstream_reason(response)
    logger.warning("Upload-Post 拒绝请求: %s %s → %s", method, path, response.status_code)
    if response.status_code in (401, 403):
        return UnprocessableError("social_publish_credentials_rejected")
    if response.status_code == 429:
        return ServiceUnavailableError("social_publish_quota_exceeded").with_diagnostic(reason)
    if response.status_code < 500:
        return UnprocessableError("social_publish_rejected").with_diagnostic(reason)
    return BadGatewayError("social_publish_upstream_failed")


def _upstream_reason(response: httpx.Response) -> str | None:
    """取上游自述的拒因。

    只认 ``message`` / ``error`` 两个字段：它们是上游文档里写明的错误载体，随响应体原样
    下发给客户端，因此不能把整段未知结构当成拒因回传。
    """
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    for field in ("message", "error"):
        text = _text(cast(dict[str, Any], payload).get(field))
        if text:
            return text[:_REASON_TRUNCATE]
    return None


def _decode(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise BadGatewayError("social_publish_upstream_malformed") from exc
    if not isinstance(payload, dict):
        raise BadGatewayError("social_publish_upstream_malformed")
    return cast(dict[str, Any], payload)


def _parse_profile(entry: dict[str, Any]) -> PublishProfile:
    raw_accounts = entry.get("social_accounts")
    accounts: list[ConnectedAccount] = []
    if isinstance(raw_accounts, dict):
        for platform, value in cast(dict[str, Any], raw_accounts).items():
            # 未连接的平台上游回空串而不是省略键，空值即「没连」。
            if not isinstance(value, dict):
                continue
            account = cast(dict[str, Any], value)
            accounts.append(
                ConnectedAccount(
                    platform=platform,
                    display_name=_text(account.get("display_name")) or "",
                    handle=_text(account.get("handle")) or "",
                    reauth_required=bool(account.get("reauth_required")),
                )
            )
    return PublishProfile(username=_text(entry.get("username")) or "", accounts=tuple(accounts))


def _parse_outcome(entry: dict[str, Any]) -> PlatformOutcome:
    raw_status = _text(entry.get("status"))
    success = bool(entry.get("success"))
    if raw_status is None:
        # 进度接口在排队阶段可能只给 success 标志，缺状态时按它归档。
        status: PlatformStatus = "completed" if success else "processing"
    elif raw_status in _KNOWN_PLATFORM_STATUSES:
        status = cast(PlatformStatus, raw_status)
    else:
        # 认不出的措辞曾按「处理中」收下，代价是顶层已 completed、读侧却把这个平台
        # 永远画成转圈：轮询已经停了，那个图标再也不会变。宁可判畸形。
        raise BadGatewayError("social_publish_upstream_malformed")
    if bool(entry.get("skipped")):
        status = "skipped"
    return PlatformOutcome(
        platform=_text(entry.get("platform")) or "",
        status=status,
        success=success,
        url=_first_url(entry),
        # 上游拒因是社交平台自己的措辞，用户按它去修；只做长度封顶，不放行整页错误内容。
        error=_truncate(_text(entry.get("error"))),
    )


def _first_url(entry: dict[str, Any]) -> str | None:
    for field in _URL_FIELDS:
        value = _text(entry.get(field))
        # TikTok 投递到收件箱时把说明文字写在 post_url 里，不是链接，不当成品地址回传。
        if value and value.startswith("http"):
            return value
    return None


def _parse_status(value: object) -> SubmissionStatus:
    """聚合状态必须是契约里的那几个之一。

    缺失或不认得的状态曾按 ``pending`` 收下，代价是调用方拿着一个永远不会转终态的值
    一直轮询下去——畸形响应就此绕过 ``social_publish_upstream_malformed`` 静默生效。
    上游新增状态时先把它显式写进契约，再放行。
    """
    text = _text(value)
    if text in _KNOWN_STATUSES:
        return cast(SubmissionStatus, text)
    raise BadGatewayError("social_publish_upstream_malformed")


def _truncate(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:_REASON_TRUNCATE]


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


__all__ = ["DEFAULT_BASE_URL", "UploadPostClient"]
