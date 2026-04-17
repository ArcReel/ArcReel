"""
Rate limiting middleware for ArcReel API.
Bảo vệ các endpoint tốn tài nguyên khỏi bị spam.
Protects resource-intensive endpoints from abuse.
保护资源密集型端点免受滥用。

Sử dụng slowapi (wrapper cho limits). Không thay đổi logic gốc, chỉ thêm layer bảo vệ.
Uses slowapi (limits wrapper). Does NOT change original logic — additive protection layer only.
"""

import logging
import os

from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def _get_key(request: Request) -> str:
    """
    Ưu tiên dùng API key làm rate-limit key, fallback sang IP.
    Prefer API key as rate-limit key, fallback to IP.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        # Dùng 8 ký tự đầu của token làm key (đủ unique, không lộ toàn bộ)
        token = auth_header[7:]
        return f"apikey:{token[:8]}"
    return get_remote_address(request)


# Đọc rate limit từ env, default vừa phải cho single-user
# Read from env, sensible defaults for single-user
RATE_LIMIT_AGENT = os.environ.get("RATE_LIMIT_AGENT", "30/minute")
RATE_LIMIT_GENERATE = os.environ.get("RATE_LIMIT_GENERATE", "60/minute")
RATE_LIMIT_UPLOAD = os.environ.get("RATE_LIMIT_UPLOAD", "20/minute")
RATE_LIMIT_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "200/minute")

# Create limiter instance
limiter = Limiter(
    key_func=_get_key,
    default_limits=[RATE_LIMIT_DEFAULT],
    storage_uri="memory://",  # In-memory cho dev; production nên dùng Redis
)


def _custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """
    Trả JSON thân thiện thay vì HTML mặc định.
    Return friendly JSON instead of default HTML.
    """
    retry_after = exc.detail or "unknown"
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": f"Too many requests. Retry after {retry_after}.",
            "detail": str(exc.detail),
        },
        headers={"Retry-After": str(retry_after)},
    )


def setup_rate_limiter(app: FastAPI) -> None:
    """
    Đăng ký rate limiter vào app.
    Gọi hàm này trong app.py SAU khi tạo FastAPI instance.
    Register rate limiter onto the app.
    Call this in app.py AFTER creating the FastAPI instance.

    Usage:
        from server.rate_limiter import setup_rate_limiter
        setup_rate_limiter(app)
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_handler)
    logger.info(
        "Rate limiter enabled: agent=%s, generate=%s, upload=%s, default=%s",
        RATE_LIMIT_AGENT,
        RATE_LIMIT_GENERATE,
        RATE_LIMIT_UPLOAD,
        RATE_LIMIT_DEFAULT,
    )
