"""
认证 API 路由

提供 OAuth2 登录和 token 验证接口。
"""

import logging
import secrets
from typing import Annotated, Literal

from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from lib.db import get_async_session
from lib.db.base import DEFAULT_USER_ID
from lib.db.models.user import User
from lib.i18n import Translator
from server.auth import (
    CurrentUser,
    check_credentials,
    create_token,
    is_auth_enabled,
)
from server.services.account_center import (
    ROLE_CATALOG,
    AccountCenterError,
    account_center_config,
    account_center_enabled,
    complete_setup,
    create_login_ticket,
    exchange_linked_ticket,
    fetch_center_identity,
    fetch_center_session_identity,
    frontend_redirect,
    inspect_setup_ticket,
    oauth_client,
)
from server.services.arcreel_cloud import ArcReelCloudError, cloud_enabled, login_with_cloud

logger = logging.getLogger(__name__)

router = APIRouter()

# 公开端点：拿到 token 之前必须可达，注册时不挂 Bearer 依赖。
public_router = APIRouter()


# ==================== 响应模型 ====================


class AuthenticatedUser(BaseModel):
    id: str
    username: str
    role: str
    display_name: str | None = None
    identity_source: str = "internal"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: AuthenticatedUser


class VerifyResponse(BaseModel):
    valid: bool
    username: str
    id: str
    role: str


class AuthStatusResponse(BaseModel):
    enabled: bool
    account_center_enabled: bool


class AccountCenterTicketRequest(BaseModel):
    ticket: str


class AccountCenterSetupRequest(AccountCenterTicketRequest):
    mode: Literal["auto", "bind"]
    username: str | None = None
    password: str | None = None


class AccountCenterSetupInfo(BaseModel):
    username: str
    display_name: str | None
    roles: list[str]


class AccountCenterDirectLaunchRequest(BaseModel):
    return_to: str = "/app/projects"


class AccountCenterDirectLaunchResponse(BaseModel):
    redirect_url: str


# ==================== 路由 ====================


@public_router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status():
    """暴露 ``AUTH_ENABLED`` 状态供前端 bootstrap 判断是否需要登录拦截。

    前端 ``auth-store.initialize()`` 在 localStorage 无 token 时调用本接口：
    ``enabled=false`` 时跳过登录页直接进主界面；``enabled=true`` 时保留原
    登录链路。本接口本身**不要求认证**——一个 boolean 比 401 探针更直观，
    且实际"是否需要登录"通过 401/200 也能从外部观察到，因此不增量泄露。
    """
    return AuthStatusResponse(
        enabled=is_auth_enabled(),
        account_center_enabled=account_center_enabled() and not cloud_enabled(),
    )


@public_router.post("/auth/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """用户登录

    使用 OAuth2 标准表单格式验证凭据，成功返回 access_token。
    ``AUTH_ENABLED=false`` 时跳过凭据校验，直接签发 token，让前端
    LoginPage 即便被打开也能正常跳转主界面。
    """
    if is_auth_enabled() and cloud_enabled():
        try:
            user = await login_with_cloud(session, form_data.username, form_data.password)
        except ArcReelCloudError as exc:
            logger.warning("ArcReel 云账号登录失败 user=%s code=%s", form_data.username, exc.code)
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
                headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
            ) from exc
        token = create_token(
            user.username,
            user_id=user.id,
            role=user.role,
            identity_source="arcreel_cloud",
        )
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            user=AuthenticatedUser(
                id=user.id,
                username=user.username,
                role=user.role,
                display_name=user.display_name,
                identity_source="arcreel_cloud",
            ),
        )

    if is_auth_enabled() and not check_credentials(form_data.username, form_data.password):
        logger.warning("登录失败: 用户名或密码错误 (用户: %s)", form_data.username)
        raise HTTPException(
            status_code=401,
            detail=_t("unauthorized"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_token(form_data.username, user_id=DEFAULT_USER_ID, role="admin")
    logger.info("用户登录成功: %s", form_data.username)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=AuthenticatedUser(
            id=DEFAULT_USER_ID,
            username=form_data.username,
            role="admin",
            identity_source="internal",
        ),
    )


@public_router.get("/auth/account-center/start")
async def start_account_center_login(request: Request, return_to: str = Query("/app/projects")):
    """Start a fresh OIDC authorization transaction with PKCE, state, and nonce."""
    safe_return_to = return_to if return_to.startswith("/app/") and not return_to.startswith("//") else "/app/projects"
    request.session["account_center_return_to"] = safe_return_to
    try:
        client = oauth_client()
        return await client.authorize_redirect(
            request,
            account_center_config().redirect_uri,
            nonce=secrets.token_urlsafe(24),
        )
    except AccountCenterError as exc:
        return _account_center_error_redirect(exc)


@public_router.post("/auth/account-center/direct", response_model=AccountCenterDirectLaunchResponse)
async def direct_account_center_login(
    body: AccountCenterDirectLaunchRequest,
    authorization: str | None = Header(None),
):
    """Reuse the authenticated center workbench session without a second login."""
    access_token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not access_token:
        raise _as_http_error(AccountCenterError("未提供账号中心登录凭证", 401, "TOKEN_MISSING"))
    safe_return_to = (
        body.return_to
        if body.return_to.startswith("/app/") and not body.return_to.startswith("//")
        else "/app/projects"
    )
    try:
        identity = await fetch_center_session_identity(access_token)
        ticket, purpose = await create_login_ticket(identity, access_token)
    except AccountCenterError as exc:
        raise _as_http_error(exc) from exc
    path = "/auth/account-center/callback" if purpose == "exchange" else "/account-center/setup"
    return AccountCenterDirectLaunchResponse(
        redirect_url=frontend_redirect(path, ticket=ticket, return_to=safe_return_to),
    )


@public_router.get("/auth/account-center/portal")
async def go_to_account_center_portal():
    """Open the account-center login page without attempting reverse automatic SSO."""
    config = account_center_config(require_oauth=False)
    if not config.portal_url:
        return _account_center_error_redirect(
            AccountCenterError("数据中台登录地址尚未配置", 503, "ACCOUNT_CENTER_PORTAL_NOT_CONFIGURED")
        )
    return RedirectResponse(config.portal_url, status_code=302)


@public_router.get("/auth/account-center/callback")
async def account_center_callback(request: Request):
    """Validate the OIDC callback and hand the SPA a one-time opaque ticket."""
    try:
        token = await oauth_client().authorize_access_token(request)
        access_token = str(token.get("access_token") or "")
        if not access_token:
            raise AccountCenterError("账号中心未返回访问令牌", 401, "TOKEN_INVALID")
        identity = await fetch_center_identity(access_token)
        ticket, purpose = await create_login_ticket(identity, access_token)
        return_to = str(request.session.pop("account_center_return_to", "/app/projects"))
        path = "/auth/account-center/callback" if purpose == "exchange" else "/account-center/setup"
        return RedirectResponse(frontend_redirect(path, ticket=ticket, return_to=return_to), status_code=302)
    except OAuthError as exc:
        logger.warning("账号中心 OIDC 回调失败: %s", exc.error)
        return _account_center_error_redirect(
            AccountCenterError("账号中心授权请求无效或已过期", 401, "OIDC_CALLBACK_INVALID")
        )
    except AccountCenterError as exc:
        return _account_center_error_redirect(exc)


@public_router.post("/auth/account-center/exchange", response_model=TokenResponse)
async def exchange_account_center_ticket(body: AccountCenterTicketRequest):
    try:
        token, user = await exchange_linked_ticket(body.ticket)
    except AccountCenterError as exc:
        raise _as_http_error(exc) from exc
    return _center_token_response(token, user)


@public_router.get("/auth/account-center/setup", response_model=AccountCenterSetupInfo)
async def get_account_center_setup(ticket: str = Query(...)):
    try:
        identity = await inspect_setup_ticket(ticket)
    except AccountCenterError as exc:
        raise _as_http_error(exc) from exc
    return AccountCenterSetupInfo(
        username=identity.username,
        display_name=identity.display_name,
        roles=list(identity.roles),
    )


@public_router.post("/auth/account-center/setup", response_model=TokenResponse)
async def setup_account_center_identity(body: AccountCenterSetupRequest):
    try:
        token, user = await complete_setup(
            body.ticket,
            body.mode,
            username=body.username,
            password=body.password,
        )
    except AccountCenterError as exc:
        raise _as_http_error(exc) from exc
    return _center_token_response(token, user)


@public_router.get("/account-center/roles")
async def account_center_roles(authorization: str | None = Header(None)):
    provided = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not provided:
        raise HTTPException(status_code=401, detail={"code": "INTEGRATION_TOKEN_INVALID", "message": "集成凭证无效"})
    config = account_center_config(require_oauth=False)
    expected = config.integration_token
    if not expected:
        raise HTTPException(
            status_code=503, detail={"code": "ROLE_CATALOG_NOT_CONFIGURED", "message": "角色目录凭证未配置"}
        )
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail={"code": "INTEGRATION_TOKEN_INVALID", "message": "集成凭证无效"})
    return {
        "system_id": config.system_id,
        "roles": [{**role, "updated_at": "2026-08-24T00:00:00Z"} for role in ROLE_CATALOG],
    }


@router.get("/auth/verify", response_model=VerifyResponse)
async def verify(
    current_user: CurrentUser,
):
    """验证 token 有效性

    使用 OAuth2 Bearer token 依赖自动提取和验证 token。
    """
    return VerifyResponse(
        valid=True,
        username=current_user.sub,
        id=current_user.id,
        role=current_user.role,
    )


def _center_token_response(token: str, user: User) -> TokenResponse:
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=AuthenticatedUser(
            id=user.id,
            username=user.username,
            role=user.role,
            display_name=user.display_name,
            identity_source="account_center",
        ),
    )


def _as_http_error(exc: AccountCenterError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})


def _account_center_error_redirect(exc: AccountCenterError) -> RedirectResponse:
    return RedirectResponse(
        frontend_redirect("/login", account_center_error=exc.code, message=str(exc)),
        status_code=302,
    )
