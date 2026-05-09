"""
认证 API 路由

提供 OAuth2 登录和 token 验证接口。
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from lib.i18n import Translator
from server.auth import CurrentUser, create_token
from server.fork_auth import authenticate  # fork-private: 多用户登录

logger = logging.getLogger(__name__)

router = APIRouter()


# ==================== 响应模型 ====================


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class VerifyResponse(BaseModel):
    valid: bool
    username: str
    role: str = "admin"  # fork-private


class MeResponse(BaseModel):
    """fork-private: 当前登录用户元信息。"""

    id: str
    username: str
    role: str


# ==================== 路由 ====================


@router.post("/auth/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    _t: Translator,
):
    """用户登录

    使用 OAuth2 标准表单格式验证凭据，成功返回 access_token。
    """
    user = await authenticate(form_data.username, form_data.password)
    if user is None:
        logger.warning("登录失败: 用户名或密码错误 (用户: %s)", form_data.username)
        raise HTTPException(
            status_code=401,
            detail=_t("unauthorized"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_token(user["username"], role=user["role"])
    logger.info("用户登录成功: %s", user["username"])
    return TokenResponse(access_token=token, token_type="bearer")


@router.get("/auth/verify", response_model=VerifyResponse)
async def verify(
    current_user: CurrentUser,
):
    """验证 token 有效性

    使用 OAuth2 Bearer token 依赖自动提取和验证 token。
    """
    return VerifyResponse(valid=True, username=current_user.sub, role=current_user.role)


@router.get("/auth/me", response_model=MeResponse)
async def me(current_user: CurrentUser):
    """返回当前登录用户的元信息（id / username / role）。"""
    return MeResponse(id=current_user.id, username=current_user.sub, role=current_user.role)
