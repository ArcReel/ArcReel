"""Account-center OIDC, identity binding, and role mapping.

The account center owns authentication and system-role assignment. ArcReel keeps
its local user primary key and issues its existing local JWT after a verified,
single-use OIDC handoff.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from urllib.parse import urlencode

import httpx
from authlib.integrations.starlette_client import OAuth
from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db import async_session_factory
from lib.db.base import utc_now
from lib.db.models.user import AccountCenterLoginTicket, User
from server.auth import check_credentials, create_token

SYSTEM_ID = "arcreel"
ADMIN_ROLE_CODE = "ARCREEL_ADMIN"
USER_ROLE_CODE = "ARCREEL_USER"
ROLE_CATALOG = (
    {
        "code": ADMIN_ROLE_CODE,
        "name": "ArcReel 管理员",
        "description": "可使用全部创作能力，并管理供应商、系统配置、API Key、日志与用量。",
        "permission_count": 2,
        "status": "active",
    },
    {
        "code": USER_ROLE_CODE,
        "name": "ArcReel 创作用户",
        "description": "可使用项目、素材、生成和 Agent 等创作功能。",
        "permission_count": 1,
        "status": "active",
    },
)

_TICKET_TTL = timedelta(minutes=5)
_oauth = OAuth()
_registered_client_key: tuple[str, str, str] | None = None


class AccountCenterError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "ACCOUNT_CENTER_ERROR") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class AccountCenterConfig:
    issuer_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    frontend_url: str
    portal_url: str
    publishable_key: str
    userinfo_url: str
    integration_token: str
    system_id: str


@dataclass(frozen=True)
class CenterIdentity:
    sub: str
    username: str
    display_name: str | None
    contact_email: str | None
    roles: tuple[str, ...]


def account_center_config(*, require_oauth: bool = True) -> AccountCenterConfig:
    issuer = os.environ.get("ACCOUNT_CENTER_ISSUER_URL", "").strip().rstrip("/")
    client_id = os.environ.get("ACCOUNT_CENTER_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ACCOUNT_CENTER_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("ACCOUNT_CENTER_REDIRECT_URI", "").strip()
    frontend_url = os.environ.get("ACCOUNT_CENTER_FRONTEND_URL", "http://127.0.0.1:5173").strip().rstrip("/")
    portal_url = os.environ.get("ACCOUNT_CENTER_PORTAL_URL", "").strip()
    publishable_key = os.environ.get("ACCOUNT_CENTER_PUBLISHABLE_KEY", "").strip()
    integration_token = os.environ.get("ACCOUNT_CENTER_INTEGRATION_TOKEN", "").strip()
    system_id = os.environ.get("ACCOUNT_CENTER_SYSTEM_ID", SYSTEM_ID).strip() or SYSTEM_ID
    userinfo_url = os.environ.get("ACCOUNT_CENTER_USERINFO_URL", "").strip()
    if issuer and not userinfo_url:
        supabase_root = issuer.removesuffix("/auth/v1")
        userinfo_url = f"{supabase_root}/functions/v1/center-userinfo"

    if require_oauth:
        missing = [
            name
            for name, value in (
                ("ACCOUNT_CENTER_ISSUER_URL", issuer),
                ("ACCOUNT_CENTER_CLIENT_ID", client_id),
                ("ACCOUNT_CENTER_REDIRECT_URI", redirect_uri),
                ("ACCOUNT_CENTER_PUBLISHABLE_KEY", publishable_key),
            )
            if not value
        ]
        if missing:
            raise AccountCenterError(
                f"账号中心尚未完成配置：{', '.join(missing)}",
                503,
                "ACCOUNT_CENTER_NOT_CONFIGURED",
            )

    return AccountCenterConfig(
        issuer_url=issuer,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        frontend_url=frontend_url,
        portal_url=portal_url,
        publishable_key=publishable_key,
        userinfo_url=userinfo_url,
        integration_token=integration_token,
        system_id=system_id,
    )


def account_center_enabled() -> bool:
    try:
        account_center_config()
    except AccountCenterError:
        return False
    return True


def oauth_client():
    """Return the Authlib OIDC client, rebuilding it only when config changes."""
    global _oauth, _registered_client_key
    config = account_center_config()
    key = (config.issuer_url, config.client_id, config.client_secret)
    if _registered_client_key != key:
        _oauth = OAuth()
        client_kwargs = {
            "scope": "openid profile email phone",
            "code_challenge_method": "S256",
        }
        if not config.client_secret:
            client_kwargs["token_endpoint_auth_method"] = "none"
        if config.client_secret:
            _oauth.register(
                name="account_center",
                client_id=config.client_id,
                client_secret=config.client_secret,
                server_metadata_url=f"{config.issuer_url}/.well-known/openid-configuration",
                client_kwargs=client_kwargs,
            )
        else:
            _oauth.register(
                name="account_center",
                client_id=config.client_id,
                server_metadata_url=f"{config.issuer_url}/.well-known/openid-configuration",
                client_kwargs=client_kwargs,
            )
        _registered_client_key = key
    client = _oauth.create_client("account_center")
    if client is None:
        raise AccountCenterError("账号中心 OIDC 客户端初始化失败", 503, "ACCOUNT_CENTER_NOT_CONFIGURED")
    return client


def frontend_redirect(path: str, **query: str) -> str:
    config = account_center_config(require_oauth=False)
    suffix = f"?{urlencode(query)}" if query else ""
    return f"{config.frontend_url}{path}{suffix}"


async def fetch_center_identity(access_token: str) -> CenterIdentity:
    config = account_center_config()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": config.publishable_key,
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(config.userinfo_url, headers=headers)
    except httpx.HTTPError as exc:
        raise AccountCenterError(
            "账号中心暂时不可用，请稍后重试",
            503,
            "IDENTITY_PROVIDER_UNAVAILABLE",
        ) from exc
    if response.status_code == 403:
        raise AccountCenterError("当前账号没有 ArcReel 访问权限", 403, "SYSTEM_ACCESS_DENIED")
    if not response.is_success:
        raise AccountCenterError("账号中心身份校验失败", 401, "TOKEN_INVALID")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AccountCenterError("账号中心返回了无效响应", 503, "IDENTITY_PROVIDER_UNAVAILABLE") from exc

    return _identity_from_userinfo(payload, config.system_id)


async def fetch_center_session_identity(access_token: str) -> CenterIdentity:
    """Validate a first-party center session used from the center workbench.

    Supabase's OAuth authorization UI is anchored to one configured Site URL.
    During local development that origin differs from the local center frontend,
    so its browser session cannot be reused there. The workbench can instead
    hand its bearer session directly to this backend; the backend validates it
    against center-admin/me and accepts only this configured subsystem's grant.
    """
    config = account_center_config()
    center_root = config.issuer_url.removesuffix("/auth/v1")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": config.publishable_key,
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{center_root}/functions/v1/center-admin/me", headers=headers)
    except httpx.HTTPError as exc:
        raise AccountCenterError(
            "账号中心暂时不可用，请稍后重试",
            503,
            "IDENTITY_PROVIDER_UNAVAILABLE",
        ) from exc
    if response.status_code == 403:
        raise AccountCenterError("当前账号没有 ArcReel 访问权限", 403, "SYSTEM_ACCESS_DENIED")
    if not response.is_success:
        raise AccountCenterError("账号中心登录状态已失效，请重新登录", 401, "TOKEN_INVALID")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AccountCenterError("账号中心返回了无效响应", 503, "IDENTITY_PROVIDER_UNAVAILABLE") from exc
    return _identity_from_center_me(payload, config.system_id)


def _identity_from_userinfo(payload: object, expected_system_id: str) -> CenterIdentity:
    if not isinstance(payload, dict):
        raise AccountCenterError("账号中心返回了无效响应", 503, "IDENTITY_PROVIDER_UNAVAILABLE")
    system_id = str(payload.get("system_id") or "").strip()
    if system_id != expected_system_id:
        raise AccountCenterError("账号中心身份信息不完整或系统标识不匹配", 403, "SYSTEM_ACCESS_DENIED")
    return _build_center_identity(
        sub=payload.get("sub"),
        username=payload.get("username"),
        display_name=payload.get("name"),
        contact_email=payload.get("email"),
        raw_roles=payload.get("roles"),
    )


def _identity_from_center_me(payload: object, expected_system_id: str) -> CenterIdentity:
    if not isinstance(payload, dict) or not isinstance(payload.get("profile"), dict):
        raise AccountCenterError("账号中心返回了无效响应", 503, "IDENTITY_PROVIDER_UNAVAILABLE")
    raw_systems = payload.get("systems")
    if not isinstance(raw_systems, list):
        raise AccountCenterError("账号中心身份信息不完整", 403, "SYSTEM_ACCESS_DENIED")
    system = next(
        (
            item
            for item in raw_systems
            if isinstance(item, dict) and str(item.get("system_id") or "").strip() == expected_system_id
        ),
        None,
    )
    if system is None:
        raise AccountCenterError("当前账号没有 ArcReel 访问权限", 403, "SYSTEM_ACCESS_DENIED")
    profile = cast(dict[str, object], payload["profile"])
    return _build_center_identity(
        sub=profile.get("id"),
        username=profile.get("username"),
        display_name=profile.get("display_name"),
        contact_email=profile.get("contact_email"),
        raw_roles=system.get("role_codes"),
    )


def _build_center_identity(
    *,
    sub: object,
    username: object,
    display_name: object,
    contact_email: object,
    raw_roles: object,
) -> CenterIdentity:
    normalized_sub = str(sub or "").strip()
    normalized_username = str(username or "").strip()
    if not normalized_sub or not normalized_username or not isinstance(raw_roles, list):
        raise AccountCenterError("账号中心身份信息不完整", 403, "SYSTEM_ACCESS_DENIED")
    roles = tuple(sorted({str(role).strip().upper() for role in raw_roles if str(role).strip()}))
    resolve_local_role(roles)
    return CenterIdentity(
        sub=normalized_sub,
        username=normalized_username,
        display_name=_optional_string(display_name),
        contact_email=_optional_string(contact_email),
        roles=roles,
    )


def resolve_local_role(roles: tuple[str, ...] | list[str]) -> Literal["admin", "user"]:
    normalized = {role.upper() for role in roles}
    if ADMIN_ROLE_CODE in normalized:
        return "admin"
    if USER_ROLE_CODE in normalized:
        return "user"
    raise AccountCenterError("当前账号未分配有效的 ArcReel 角色", 403, "ROLE_NOT_ASSIGNED")


async def create_login_ticket(
    identity: CenterIdentity,
    access_token: str | None = None,
) -> tuple[str, Literal["exchange", "setup"]]:
    """Create a short-lived opaque ticket; linked identities get exchange mode."""
    registration = None
    if access_token:
        from server.services.account_center_sync import register_device

        registration = await register_device(access_token, identity.sub)
    async with async_session_factory() as session:
        async with session.begin():
            await _delete_expired_tickets(session)
            user = await _find_user_by_center_sub(session, identity.sub)
            purpose: Literal["exchange", "setup"] = "exchange" if user else "setup"
            if user:
                if not user.is_active:
                    raise AccountCenterError("ArcReel 本地账号已停用", 403, "ACCOUNT_DISABLED")
                _sync_user_from_center(user, identity)
            raw_ticket = secrets.token_urlsafe(32)
            session.add(
                AccountCenterLoginTicket(
                    ticket_hash=_ticket_hash(raw_ticket),
                    purpose=purpose,
                    account_center_sub=identity.sub,
                    username=identity.username,
                    display_name=identity.display_name,
                    contact_email=identity.contact_email,
                    roles=list(identity.roles),
                    local_user_id=user.id if user else None,
                    expires_at=utc_now() + _TICKET_TTL,
                    consumed_at=None,
                    device_id=registration.device_id if registration else None,
                    device_token_encrypted=registration.encrypted_token if registration else None,
                )
            )
    return raw_ticket, purpose


async def exchange_linked_ticket(raw_ticket: str) -> tuple[str, User]:
    async with async_session_factory() as session:
        async with session.begin():
            ticket = await _valid_ticket(session, raw_ticket, "exchange")
            await _consume_ticket(session, ticket)
            if not ticket.local_user_id:
                raise AccountCenterError("登录票据未关联本地账号", 409, "IDENTITY_BINDING_CONFLICT")
            user = await session.get(User, ticket.local_user_id)
            if not user or not user.is_active or user.account_center_sub != ticket.account_center_sub:
                raise AccountCenterError("账号绑定关系已失效", 409, "IDENTITY_BINDING_CONFLICT")
            from server.services.account_center_sync import attach_ticket_connection

            connection = await attach_ticket_connection(session, ticket, user)
            token = create_token(user.username, user_id=user.id, role=user.role)
            user_id = user.id
    from server.services.account_center_sync import sync_user_connection

    if connection is not None:
        await sync_user_connection(user_id)
    return token, user


async def inspect_setup_ticket(raw_ticket: str) -> CenterIdentity:
    async with async_session_factory() as session:
        ticket = await _valid_ticket(session, raw_ticket, "setup")
        return _identity_from_ticket(ticket)


async def complete_setup(
    raw_ticket: str,
    mode: Literal["auto", "bind"],
    username: str | None = None,
    password: str | None = None,
) -> tuple[str, User]:
    if mode == "bind" and (not username or password is None or not check_credentials(username, password)):
        raise AccountCenterError("本地账号或密码错误", 401, "LOCAL_CREDENTIALS_INVALID")

    async with async_session_factory() as session:
        async with session.begin():
            ticket = await _valid_ticket(session, raw_ticket, "setup")
            identity = _identity_from_ticket(ticket)
            # The identity may have been linked after this ticket was issued by
            # another browser tab. In that race, continue with the established
            # binding instead of asking the same identity to bind again.
            user = await _find_user_by_center_sub(session, identity.sub)
            if user:
                if not user.is_active:
                    raise AccountCenterError("ArcReel 本地账号已停用", 403, "ACCOUNT_DISABLED")
                _sync_user_from_center(user, identity)

            elif mode == "bind":
                user = await _find_user_by_username(session, username or "")
                if not user or not user.is_active:
                    raise AccountCenterError("本地账号不存在或已停用", 401, "LOCAL_CREDENTIALS_INVALID")
                if user.account_center_sub and user.account_center_sub != identity.sub:
                    raise AccountCenterError(
                        f"ArcReel 内部账号“{user.username}”已绑定其他数据中台账号。"
                        "当前账号不能重复占用，请选择自动创建账号，或联系管理员先解除原绑定。",
                        409,
                        "LOCAL_ACCOUNT_ALREADY_BOUND",
                    )
            elif mode == "auto":
                local_username = await _available_username(session, identity.username, identity.sub)
                user = User(
                    id=str(uuid.uuid4()),
                    username=local_username,
                    role=resolve_local_role(identity.roles),
                    is_active=True,
                )
                session.add(user)
            else:
                raise AccountCenterError("不支持的账号处理方式", 400, "INVALID_SETUP_MODE")

            _sync_user_from_center(user, identity)
            ticket.local_user_id = user.id
            from server.services.account_center_sync import attach_ticket_connection

            connection = await attach_ticket_connection(session, ticket, user)
            await _consume_ticket(session, ticket)
            await session.flush()
            token = create_token(user.username, user_id=user.id, role=user.role)
            user_id = user.id
    from server.services.account_center_sync import sync_user_connection

    if connection is not None:
        await sync_user_connection(user_id)
    return token, user


async def _valid_ticket(
    session: AsyncSession,
    raw_ticket: str,
    purpose: Literal["exchange", "setup"],
) -> AccountCenterLoginTicket:
    if not raw_ticket:
        raise AccountCenterError("缺少登录票据", 400, "LOGIN_TICKET_MISSING")
    ticket = await session.get(AccountCenterLoginTicket, _ticket_hash(raw_ticket))
    if not ticket or ticket.purpose != purpose or ticket.consumed_at is not None:
        raise AccountCenterError("登录票据无效或已使用", 401, "LOGIN_TICKET_INVALID")
    expires_at = ticket.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise AccountCenterError("登录票据已过期，请重新从中台进入", 401, "LOGIN_TICKET_EXPIRED")
    return ticket


async def _delete_expired_tickets(session: AsyncSession) -> None:
    await session.execute(delete(AccountCenterLoginTicket).where(AccountCenterLoginTicket.expires_at < utc_now()))


async def _consume_ticket(session: AsyncSession, ticket: AccountCenterLoginTicket) -> None:
    """Atomically consume a ticket so concurrent callbacks cannot mint two sessions."""
    result = cast(
        CursorResult,
        await session.execute(
            update(AccountCenterLoginTicket)
            .where(
                AccountCenterLoginTicket.ticket_hash == ticket.ticket_hash,
                AccountCenterLoginTicket.consumed_at.is_(None),
            )
            .values(consumed_at=utc_now())
        ),
    )
    if result.rowcount != 1:
        raise AccountCenterError("登录票据无效或已使用", 401, "LOGIN_TICKET_INVALID")


async def _find_user_by_center_sub(session: AsyncSession, sub: str) -> User | None:
    result = await session.execute(select(User).where(User.account_center_sub == sub))
    return result.scalar_one_or_none()


async def _find_user_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def _available_username(session: AsyncSession, preferred: str, sub: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "-", preferred).strip("-.")[:48] or "center-user"
    if await _find_user_by_username(session, base) is None:
        return base
    candidate = f"{base[:39]}-{sub.replace('-', '')[:8]}"
    if await _find_user_by_username(session, candidate) is None:
        return candidate
    return f"{base[:31]}-{uuid.uuid4().hex[:12]}"


def _sync_user_from_center(user: User, identity: CenterIdentity) -> None:
    user.account_center_sub = identity.sub
    user.account_center_roles = list(identity.roles)
    user.account_center_synced_at = utc_now()
    user.display_name = identity.display_name
    user.contact_email = identity.contact_email
    user.role = resolve_local_role(identity.roles)


def _identity_from_ticket(ticket: AccountCenterLoginTicket) -> CenterIdentity:
    return CenterIdentity(
        sub=ticket.account_center_sub,
        username=ticket.username,
        display_name=ticket.display_name,
        contact_email=ticket.contact_email,
        roles=tuple(ticket.roles),
    )


def _ticket_hash(raw_ticket: str) -> str:
    return hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()


def _optional_string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
