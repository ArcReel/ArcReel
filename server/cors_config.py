"""Env-driven CORS origin policy, shared by the app middleware and the remote MCP mount."""

from __future__ import annotations

import os

WILDCARD = "*"


def resolve_cors_policy(raw: str | None = None) -> tuple[list[str], bool]:
    """Parse ``CORS_ORIGINS`` into ``(allow_origins, allow_credentials)``.

    未设置 / 空 / 含 ``*`` → 通配 origins 且 credentials 强制关闭（CORS spec 不允许通配 +
    credentials 组合，Starlette 在初始化时会 RuntimeError）；否则按逗号分隔解析为白名单，
    credentials 打开供前端附带 cookie / Authorization 跨域。
    """
    raw = os.environ.get("CORS_ORIGINS", WILDCARD) if raw is None else raw
    origins = [origin.strip() for origin in raw.strip().split(",") if origin.strip()]
    if not origins or WILDCARD in origins:
        return [WILDCARD], False
    return origins, True


__all__ = ["WILDCARD", "resolve_cors_policy"]
