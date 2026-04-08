"""
Authentication API routes

Provides OAuth2 login and token verification endpoints.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from server.auth import CurrentUser, check_credentials, create_token

logger = logging.getLogger(__name__)

router = APIRouter()


# ==================== Response models ====================


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class VerifyResponse(BaseModel):
    valid: bool
    username: str


# ==================== Routes ====================


@router.post("/auth/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
):
    """User login

    Validates credentials using the OAuth2 standard form format and returns an access_token on success.
    """
    if not check_credentials(form_data.username, form_data.password):
        logger.warning("Login failed: incorrect username or password (user: %s)", form_data.username)
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_token(form_data.username)
    logger.info("User logged in successfully: %s", form_data.username)
    return TokenResponse(access_token=token, token_type="bearer")


@router.get("/auth/verify", response_model=VerifyResponse)
async def verify(
    current_user: CurrentUser,
):
    """Verify token validity

    Uses the OAuth2 Bearer token dependency to automatically extract and verify the token.
    """
    return VerifyResponse(valid=True, username=current_user.sub)
