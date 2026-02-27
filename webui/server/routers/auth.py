"""
认证 API 路由

提供登录和 token 验证接口。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from webui.server.auth import check_credentials, create_token, verify_token

logger = logging.getLogger(__name__)

router = APIRouter()


# ==================== 请求/响应模型 ====================


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


class VerifyResponse(BaseModel):
    valid: bool
    username: str


# ==================== 路由 ====================


@router.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """用户登录

    验证用户名密码，成功返回 JWT token。
    """
    if not check_credentials(req.username, req.password):
        logger.warning("登录失败: 用户名或密码错误 (用户: %s)", req.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_token(req.username)
    logger.info("用户登录成功: %s", req.username)
    return LoginResponse(token=token, username=req.username)


@router.get("/auth/verify", response_model=VerifyResponse)
async def verify(authorization: Optional[str] = Header(None)):
    """验证 token 有效性

    从 Authorization header 提取 Bearer token 并验证。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少认证 token")

    token = authorization.removeprefix("Bearer ").strip()
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="无效或过期的 token")

    return VerifyResponse(valid=True, username=payload["sub"])
