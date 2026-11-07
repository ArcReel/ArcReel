"""Env-driven CORS origin policy, shared by the app middleware and the remote MCP mount."""

from __future__ import annotations

import os

WILDCARD = "*"


def resolve_cors_policy(raw: str | None = None) -> tuple[list[str], bool]:
    """Parse ``CORS_ORIGINS`` into ``(allow_origins, allow_credentials)``.

    Unset / empty / containing ``*`` means wildcard origins with credentials forced off (CORS forbids wildcard +
    credentials; Starlette raises RuntimeError at initialization). Otherwise parse a comma-separated whitelist,
    with credentials enabled for cross-origin frontend cookie / Authorization use.
    """
    raw = os.environ.get("CORS_ORIGINS", WILDCARD) if raw is None else raw
    origins = [origin.strip() for origin in raw.strip().split(",") if origin.strip()]
    if not origins or WILDCARD in origins:
        return [WILDCARD], False
    return origins, True


__all__ = ["WILDCARD", "resolve_cors_policy"]
