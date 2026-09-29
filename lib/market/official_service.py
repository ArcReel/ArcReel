"""官方服务：地址与开关、实例标识，以及经共享 HTTP 客户端的出站调用。

官方服务是市场之上的覆盖层（``docs/adr/0093``）。地址随版内置，可由环境变量
``ARCREEL_OFFICIAL_SERVICE_URL`` 覆盖，覆盖为空即关闭；用户可见的总开关、首次告知标记与实例标识存系统设置。
地址为空或开关关闭时 :meth:`OfficialServiceGateway.open` 返回 None，调用方据此对官方服务零出站。

出站复用共享 httpx 客户端，单请求使用独立短超时、不重试。官方服务的非 2xx 响应体 ``{code, params}`` 转为
:class:`OfficialServiceError`：状态码原样，文案键为 ``official_service_error_<code>``（目录未登记的 code
落到通用键），``code`` 与 ``params`` 原样挂在诊断上；取不回或响应不可用时为 502 ``official_service_unavailable``。
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.repository import SystemSettingRepository
from lib.db.models.config import SystemSetting
from lib.i18n import DEFAULT_LOCALE, MESSAGES
from lib.infra.api_errors import ApiError, BadGatewayError, ConflictError
from lib.infra.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

DEFAULT_OFFICIAL_SERVICE_URL = "https://arc-reel.com"
OFFICIAL_SERVICE_URL_ENV = "ARCREEL_OFFICIAL_SERVICE_URL"
#: 系统设置：用户可见的总开关，``"false"`` 为关闭，缺省为开。
ENABLED_SETTING = "official_service_enabled"
#: 系统设置：首次进入市场页的说明已确认。
NOTICE_SEEN_SETTING = "official_service_notice_seen"
#: 系统设置：实例标识（UUIDv4），首次出站时生成，重置即删除。
INSTANCE_ID_SETTING = "official_service_instance_id"
INSTANCE_HEADER = "X-ArcReel-Instance"
REQUEST_TIMEOUT_SECONDS = 3.0
#: 官方服务批量查询聚合的单次条目上限。
AGGREGATES_BATCH_SIZE = 100

_ERROR_KEY_PREFIX = "official_service_error_"
_UNKNOWN_ERROR_KEY = "official_service_error_unknown"


def official_service_url() -> str:
    """环境变量存在（含空串）即以它为准；为空表示关闭。"""
    raw = os.environ.get(OFFICIAL_SERVICE_URL_ENV)
    return (DEFAULT_OFFICIAL_SERVICE_URL if raw is None else raw).strip().rstrip("/")


class OfficialServiceError(ApiError):
    """官方服务以 ``{code, params}`` 拒绝了请求；状态码与 code 原样带回本地响应。"""

    def __init__(self, status_code: int, code: str, params: Mapping[str, Any]) -> None:
        key = _ERROR_KEY_PREFIX + code
        if key not in MESSAGES[DEFAULT_LOCALE]:
            key = _UNKNOWN_ERROR_KEY
        super().__init__(key, status_code=status_code, **{**params, "code": code})
        self.code = code
        self.remote_params = dict(params)
        self.with_diagnostic({"official_service": {"code": code, "params": self.remote_params}})


def _unavailable(reason: str) -> BadGatewayError:
    return BadGatewayError("official_service_unavailable").with_diagnostic({"reason": reason})


@dataclass(frozen=True)
class EntryRef:
    """官方服务的条目引用；``source`` 为官方市场源的规范键。"""

    type: str
    source: str
    slug: str

    def to_json(self) -> dict[str, str]:
        return {"type": self.type, "source": self.source, "slug": self.slug}


@dataclass(frozen=True)
class EntryAggregate:
    ref: EntryRef
    #: 服务端取整后的展示值。
    installs: int
    rating_count: int
    #: 评分人数不足服务端阈值时为 None。
    rating_average: float | None


@dataclass(frozen=True)
class SubmissionStatus:
    """官方服务上一次分享提交的状态；``status`` 为 ``open`` / ``merged`` / ``closed``。"""

    token: str
    type: str
    slug: str
    status: str
    pr_url: str


SUBMISSION_STATUSES = frozenset({"open", "merged", "closed"})


@dataclass(frozen=True)
class OfficialServiceState:
    #: 地址非空。
    available: bool
    #: 地址非空且总开关为开：只有此时才会对官方服务出站。
    enabled: bool
    notice_seen: bool
    #: 尚未生成时为 None。
    instance_id: str | None


class OfficialServiceClient:
    """已开启的官方服务的一次会话：每个请求携带实例标识头。"""

    def __init__(self, *, base_url: str, instance_id: str, http_client: httpx.AsyncClient, timeout: float) -> None:
        self._base_url = base_url
        self._instance_id = instance_id
        self._http_client = http_client
        self._timeout = timeout

    async def request(self, method: str, path: str, *, json: object | None = None) -> httpx.Response:
        """``path`` 相对 ``/api/v1``；2xx 原样返回，其余抛 :class:`OfficialServiceError` 或 502。"""
        try:
            response = await self._http_client.request(
                method,
                f"{self._base_url}/api/v1{path}",
                json=json,
                headers={INSTANCE_HEADER: self._instance_id},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise _unavailable(type(exc).__name__) from exc
        if response.is_success:
            return response
        try:
            body = response.json()
        except ValueError:
            body = None
        code = body.get("code") if isinstance(body, dict) else None
        params = body.get("params") if isinstance(body, dict) else None
        if not isinstance(code, str) or not code or not isinstance(params, dict):
            raise _unavailable(f"HTTP {response.status_code}")
        raise OfficialServiceError(response.status_code, code, params)

    async def report_install(self, ref: EntryRef, *, version: str, app_version: str) -> None:
        await self.request(
            "POST", "/market/installs", json={**ref.to_json(), "version": version, "app_version": app_version}
        )

    async def rate(self, ref: EntryRef, stars: int) -> None:
        await self.request("PUT", "/market/ratings", json={**ref.to_json(), "stars": stars})

    async def aggregates(self, refs: Sequence[EntryRef]) -> list[EntryAggregate]:
        """按请求顺序返回；超过单次上限时分批查询。"""
        aggregates: list[EntryAggregate] = []
        for start in range(0, len(refs), AGGREGATES_BATCH_SIZE):
            batch = refs[start : start + AGGREGATES_BATCH_SIZE]
            response = await self.request(
                "POST", "/market/aggregates", json={"items": [ref.to_json() for ref in batch]}
            )
            aggregates.extend(_parse_aggregates(response, batch))
        return aggregates

    async def create_submission(
        self, *, type: str, slug: str, files: Mapping[str, bytes], github_username: str | None
    ) -> SubmissionStatus:
        """文件内容按标准 base64 发送；同实例同 slug 的开放中提交由官方服务推到同一 PR，令牌不变。"""
        meta = {"github_username": github_username} if github_username else {}
        response = await self.request(
            "POST",
            "/market/submissions",
            json={
                "type": type,
                "slug": slug,
                "files": {path: base64.b64encode(content).decode("ascii") for path, content in files.items()},
                "meta": meta,
            },
        )
        return _parse_submission(response)

    async def submission_status(self, token: str) -> SubmissionStatus:
        return _parse_submission(await self.request("GET", f"/market/submissions/{token}"))


def _parse_submission(response: httpx.Response) -> SubmissionStatus:
    try:
        body = response.json()
        fields = {name: body[name] for name in ("token", "type", "slug", "status", "pr_url")}
        if not all(isinstance(value, str) and value for value in fields.values()):
            raise TypeError("submission field types")
        if fields["status"] not in SUBMISSION_STATUSES:
            raise ValueError(f"unknown status {fields['status']!r}")
    except (ValueError, KeyError, TypeError) as exc:
        raise _unavailable(f"malformed submission: {exc}") from exc
    return SubmissionStatus(**fields)


def _parse_aggregates(response: httpx.Response, refs: Sequence[EntryRef]) -> list[EntryAggregate]:
    try:
        items = response.json()["items"]
        if not isinstance(items, list) or len(items) != len(refs):
            raise TypeError("items length mismatch")
        parsed = []
        for ref, item in zip(refs, items, strict=True):
            installs, count, average = item["installs"], item["rating_count"], item["rating_average"]
            if not (
                type(installs) is int
                and type(count) is int
                and (average is None or (isinstance(average, int | float) and not isinstance(average, bool)))
            ):
                raise TypeError("aggregate field types")
            parsed.append(
                EntryAggregate(
                    ref=ref,
                    installs=installs,
                    rating_count=count,
                    rating_average=None if average is None else float(average),
                )
            )
    except (ValueError, KeyError, TypeError) as exc:
        raise _unavailable(f"malformed aggregates: {exc}") from exc
    return parsed


def _is_enabled(raw: str) -> bool:
    return raw.strip().lower() != "false"


class OfficialServiceGateway:
    """官方服务的开关与实例标识，以及按当前设置开出的 :class:`OfficialServiceClient`。

    生产环境经 :func:`get_official_service_gateway` 共享一个实例；设置读写使用自己的数据库会话，
    不牵连调用方的事务。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        base_url: str | None = None,
        http_client: Callable[[], httpx.AsyncClient] = get_http_client,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._base_url = official_service_url() if base_url is None else base_url.strip().rstrip("/")
        self._http_client = http_client
        self._timeout = timeout

    async def state(self) -> OfficialServiceState:
        async with self._session_factory() as session:
            settings = SystemSettingRepository(session)
            enabled = _is_enabled(await settings.get(ENABLED_SETTING))
            notice_seen = await settings.get(NOTICE_SEEN_SETTING) == "true"
            instance_id = await settings.get(INSTANCE_ID_SETTING) or None
        available = bool(self._base_url)
        return OfficialServiceState(
            available=available, enabled=available and enabled, notice_seen=notice_seen, instance_id=instance_id
        )

    async def update(self, *, enabled: bool | None = None, notice_seen: bool | None = None) -> OfficialServiceState:
        async with self._session_factory() as session:
            settings = SystemSettingRepository(session)
            if enabled is not None:
                await settings.set(ENABLED_SETTING, "true" if enabled else "false")
            if notice_seen is not None:
                await settings.set(NOTICE_SEEN_SETTING, "true" if notice_seen else "false")
            await session.commit()
        return await self.state()

    async def reset_instance_id(self) -> OfficialServiceState:
        """删除实例标识；下次出站时生成新的，官方服务把它当作新实例。"""
        async with self._session_factory() as session:
            await SystemSettingRepository(session).delete(INSTANCE_ID_SETTING)
            await session.commit()
        return await self.state()

    async def open(self) -> OfficialServiceClient | None:
        """关闭时返回 None 且不生成实例标识；开启时按需生成实例标识。"""
        if not self._base_url:
            return None
        async with self._session_factory() as session:
            settings = SystemSettingRepository(session)
            if not _is_enabled(await settings.get(ENABLED_SETTING)):
                return None
            instance_id = await settings.get(INSTANCE_ID_SETTING)
        if not instance_id:
            instance_id = await self._create_instance_id()
        return OfficialServiceClient(
            base_url=self._base_url, instance_id=instance_id, http_client=self._http_client(), timeout=self._timeout
        )

    async def require(self) -> OfficialServiceClient:
        """同 :meth:`open`，关闭时抛 409 ``official_service_disabled``。"""
        client = await self.open()
        if client is None:
            raise ConflictError("official_service_disabled")
        return client

    async def report_install(self, ref: EntryRef, *, version: str, app_version: str) -> None:
        """安装事务提交后的旁路上报：关闭时不出站；任何失败只记日志，不重试。"""
        try:
            client = await self.open()
            if client is not None:
                await client.report_install(ref, version=version, app_version=app_version)
        except Exception:
            logger.warning("Official service install report failed for %s/%s", ref.type, ref.slug, exc_info=True)

    async def _create_instance_id(self) -> str:
        """并发首次生成时以先落库者为准。"""
        candidate = str(uuid.uuid4())
        async with self._session_factory() as session:
            settings = SystemSettingRepository(session)
            try:
                session.add(SystemSetting(key=INSTANCE_ID_SETTING, value=candidate))
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return await settings.get(INSTANCE_ID_SETTING)
        return candidate


_gateway: OfficialServiceGateway | None = None


def get_official_service_gateway() -> OfficialServiceGateway:
    """进程内共享的实例：地址在进程启动后首次使用时从环境变量读定。"""
    global _gateway
    if _gateway is None:
        from lib.db import async_session_factory

        _gateway = OfficialServiceGateway(async_session_factory)
    return _gateway
