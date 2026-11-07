"""Authentication API routes.

Provide OAuth2 login and token-validation endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from server.auth import (
    CurrentUser,
    check_credentials,
    create_token,
    is_auth_enabled,
)
from server.i18n import Translator

logger = logging.getLogger(__name__)

router = APIRouter()

# Public endpoint: it must be reachable before obtaining a token, so register without a Bearer dependency.
public_router = APIRouter()


# ==================== Response models ====================


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class VerifyResponse(BaseModel):
    valid: bool
    username: str


class AuthStatusResponse(BaseModel):
    enabled: bool


# ==================== Routes ====================


@public_router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status():
    """Expose ``AUTH_ENABLED`` so frontend bootstrap can decide whether to enforce login.

    ``auth-store.initialize()`` calls this endpoint when localStorage has no token. ``enabled=false`` skips login and
    opens the main UI; ``enabled=true`` retains the login flow. The endpoint itself **requires no authentication**:
    a boolean is clearer than probing 401, and login requirements are already externally observable through 401/200,
    so this discloses no additional information."""
    return AuthStatusResponse(enabled=is_auth_enabled())


@public_router.post("/auth/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    _t: Translator,
):
    """User login.

    Validate credentials using standard OAuth2 form data and return access_token on success.
    With ``AUTH_ENABLED=false``, skip validation and issue a token directly so an opened frontend
    LoginPage can still navigate to the main UI."""
    if is_auth_enabled() and not check_credentials(form_data.username, form_data.password):
        logger.warning("登录失败: 用户名或密码错误 (用户: %s)", form_data.username)
        raise HTTPException(
            status_code=401,
            detail=_t("unauthorized"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_token(form_data.username)
    logger.info("用户登录成功: %s", form_data.username)
    return TokenResponse(access_token=token, token_type="bearer")


@router.get("/auth/verify", response_model=VerifyResponse)
async def verify(
    current_user: CurrentUser,
):
    """Validate a token.

    Use the OAuth2 Bearer dependency to extract and validate the token automatically."""
    return VerifyResponse(valid=True, username=current_user.sub)
