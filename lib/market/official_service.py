"""Official service: URL/switches, instance identity, and outbound shared-HTTP calls.

The official service overlays the market (``docs/adr/0093``). A shipped URL can be overridden
by ``ARCREEL_OFFICIAL_SERVICE_URL``; empty override disables it. User-visible master switch,
first-notice acknowledgment, and instance identity live in system settings. With empty URLs or
a disabled switch, :meth:`OfficialServiceGateway.open` returns None and callers make no outbound calls.

Reuse shared httpx clients with independent short per-request timeouts and no retries. Non-2xx
``{code, params}`` bodies become :class:`OfficialServiceError`: retain status; use
``official_service_error_<code>`` copy keys (unregistered codes fall back to generic keys), with
original ``code`` / ``params`` on diagnostics. Fetch failures/unusable responses produce
502 ``official_service_unavailable``.
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
#: System setting: user-visible master switch; ``"false"`` disables, default enabled.
ENABLED_SETTING = "official_service_enabled"
#: System setting: first market-page explanation acknowledged.
NOTICE_SEEN_SETTING = "official_service_notice_seen"
#: System setting: UUIDv4 instance identity, generated on first outbound call and deleted on reset.
INSTANCE_ID_SETTING = "official_service_instance_id"
INSTANCE_HEADER = "X-ArcReel-Instance"
REQUEST_TIMEOUT_SECONDS = 3.0
#: Maximum entries per aggregated official-service batch query.
AGGREGATES_BATCH_SIZE = 100

_ERROR_KEY_PREFIX = "official_service_error_"
_UNKNOWN_ERROR_KEY = "official_service_error_unknown"


def official_service_url() -> str:
    """Present environment values, including empty strings, take precedence; empty disables the service."""
    raw = os.environ.get(OFFICIAL_SERVICE_URL_ENV)
    return (DEFAULT_OFFICIAL_SERVICE_URL if raw is None else raw).strip().rstrip("/")


class OfficialServiceError(ApiError):
    """Official service rejected with ``{code, params}``; preserve status/code in local responses."""

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
    """Official-service entry reference; ``source`` is the official market source’s canonical key."""

    type: str
    source: str
    slug: str

    def to_json(self) -> dict[str, str]:
        return {"type": self.type, "source": self.source, "slug": self.slug}


@dataclass(frozen=True)
class EntryAggregate:
    ref: EntryRef
    #: Server-rounded display value.
    installs: int
    rating_count: int
    #: None when rating count is below the server threshold.
    rating_average: float | None


@dataclass(frozen=True)
class SubmissionStatus:
    """Last sharing submission state in the official service; ``status`` is ``open`` / ``merged`` / ``closed``."""

    token: str
    type: str
    slug: str
    status: str
    pr_url: str


SUBMISSION_STATUSES = frozenset({"open", "merged", "closed"})


@dataclass(frozen=True)
class OfficialServiceState:
    #: URL is nonempty.
    available: bool
    #: URL is nonempty and master switch enabled; only then make outbound official-service calls.
    enabled: bool
    notice_seen: bool
    #: None until generated.
    instance_id: str | None


class OfficialServiceClient:
    """One enabled official-service session, attaching instance identity headers to every request."""

    def __init__(self, *, base_url: str, instance_id: str, http_client: httpx.AsyncClient, timeout: float) -> None:
        self._base_url = base_url
        self._instance_id = instance_id
        self._http_client = http_client
        self._timeout = timeout

    async def request(self, method: str, path: str, *, json: object | None = None) -> httpx.Response:
        """``path`` is relative to ``/api/v1``; return 2xx unchanged, otherwise raise :class:`OfficialServiceError` or 502."""
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
        """Return in request order, batching queries above the per-call limit."""
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
        """Send standard-base64 file content; the official service sends open submissions from the same instance/slug to one PR, retaining the token."""
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
        return _parse_submission(response, expected={"type": type, "slug": slug})

    async def submission_status(self, token: str) -> SubmissionStatus:
        return _parse_submission(await self.request("GET", f"/market/submissions/{token}"), expected={"token": token})


def _parse_submission(response: httpx.Response, *, expected: Mapping[str, str]) -> SubmissionStatus:
    """``expected`` identifies request fields; responses belonging to another submission are unavailable."""
    try:
        body = response.json()
        fields = {name: body[name] for name in ("token", "type", "slug", "status", "pr_url")}
        if not all(isinstance(value, str) and value for value in fields.values()):
            raise TypeError("submission field types")
        if fields["status"] not in SUBMISSION_STATUSES:
            raise ValueError(f"unknown status {fields['status']!r}")
        if any(fields[name] != value for name, value in expected.items()):
            raise ValueError("submission mismatch")
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
            if EntryRef(type=item["type"], source=item["source"], slug=item["slug"]) != ref:
                raise ValueError("aggregate entry mismatch")
            installs, count, average = item["installs"], item["rating_count"], item["rating_average"]
            if not (
                type(installs) is int
                and type(count) is int
                and (average is None or (isinstance(average, int | float) and not isinstance(average, bool)))
            ):
                raise TypeError("aggregate field types")
            # Out-of-range values make responses untrustworthy: treat the entire batch as unavailable, showing no frontend numbers.
            if installs < 0 or count < 0 or (average is not None and not 1 <= average <= 5):
                raise ValueError("aggregate field range")
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
    """Official-service switch/instance identity and :class:`OfficialServiceClient` opened from current settings.

    Production shares :func:`get_official_service_gateway`. Settings use independent database
    sessions, never affecting caller transactions.
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
        """Delete instance identity; next outbound call generates a new one, treated as a new instance by the official service."""
        async with self._session_factory() as session:
            await SystemSettingRepository(session).delete(INSTANCE_ID_SETTING)
            await session.commit()
        return await self.state()

    async def open(self) -> OfficialServiceClient | None:
        """Return None without generating identity when disabled; generate identity on demand when enabled."""
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
        """Like :meth:`open`, but disabled services raise 409 ``official_service_disabled``."""
        client = await self.open()
        if client is None:
            raise ConflictError("official_service_disabled")
        return client

    async def report_install(self, ref: EntryRef, *, version: str, app_version: str) -> None:
        """Side-channel reporting after installation commit: disabled means no outbound calls; all failures only log, without retries."""
        try:
            client = await self.open()
            if client is not None:
                await client.report_install(ref, version=version, app_version=app_version)
        except Exception:
            logger.warning("Official service install report failed for %s/%s", ref.type, ref.slug, exc_info=True)

    async def _create_instance_id(self) -> str:
        """Concurrent first generation uses the first persisted identity; regenerate if it is immediately reset/deleted."""
        while True:
            candidate = str(uuid.uuid4())
            async with self._session_factory() as session:
                try:
                    session.add(SystemSetting(key=INSTANCE_ID_SETTING, value=candidate))
                    await session.commit()
                    return candidate
                except IntegrityError:
                    await session.rollback()
                    if existing := await SystemSettingRepository(session).get(INSTANCE_ID_SETTING):
                        return existing


_gateway: OfficialServiceGateway | None = None


def get_official_service_gateway() -> OfficialServiceGateway:
    """Process-shared instance; read/freeze the environment URL on first use after process startup."""
    global _gateway
    if _gateway is None:
        from lib.db import async_session_factory

        _gateway = OfficialServiceGateway(async_session_factory)
    return _gateway
