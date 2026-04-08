"""
Ark (Volcano Engine) shared utilities module

Reused by text_backends / image_backends / video_backends / providers.

Contains:
- ARK_BASE_URL — Ark API base URL
- resolve_ark_api_key — API Key resolution (with environment variable fallback)
- create_ark_client — Ark client factory
"""

from __future__ import annotations

import os

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def resolve_ark_api_key(api_key: str | None = None) -> str:
    """Resolve Ark API Key, with environment variable fallback."""
    resolved = api_key or os.environ.get("ARK_API_KEY")
    if not resolved:
        raise ValueError("Ark API Key not provided. Please configure an API Key in Global Settings → Providers.")
    return resolved


def create_ark_client(*, api_key: str | None = None):
    """Create an Ark client, validating and constructing with api_key."""
    from volcenginesdkarkruntime import Ark

    return Ark(base_url=ARK_BASE_URL, api_key=resolve_ark_api_key(api_key))
