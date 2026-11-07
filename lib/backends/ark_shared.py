"""Shared Ark (Volcengine Ark) utilities.

Reused by text_backends / image_backends / video_backends / providers.

Includes:
- ARK_BASE_URL: Volcengine Ark API base URL.
- resolve_ark_api_key: API key resolution, raising when missing without environment fallback.
- ark_base_url: strip whitespace/trailing slashes, defaulting to ARK_BASE_URL.
- create_ark_client: Ark client factory.
"""

from __future__ import annotations

import httpx

from lib.backends.transport_layer import TransportLayer, layer_sync_client

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def resolve_ark_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Ark API Key")
    return api_key.strip()


def ark_base_url(configured: str | None = None) -> str:
    """Normalize user base_url by stripping whitespace/trailing slashes; default to ARK_BASE_URL.

    Unlike dashscope/minimax/agnes, never derive hosts/rebuild paths from known suffixes. Ark backends
    pass nonstandard paths such as ark-agent-plan /api/plan/v3; suffix reconstruction would corrupt
    them. Conservatively normalize whitespace/trailing slashes without rewriting path structure.
    """
    # Strip before checking emptiness: whitespace-only strings are truthy and bypass or, so fall back after stripping.
    base = (configured or "").strip()
    return base.rstrip("/") if base else ARK_BASE_URL


def create_ark_client(
    *, api_key: str | None = None, base_url: str | None = None, transport_layer: TransportLayer | None = None
):
    """Create Ark clients with ARK_BASE_URL (/api/v3) defaults, normalized through ark_base_url.

    The SDK client is synchronous; ``transport_layer`` layers over synchronous httpx clients.
    """
    from volcenginesdkarkruntime import Ark

    base = ark_base_url(base_url)
    key = resolve_ark_api_key(api_key)
    if transport_layer is None:
        return Ark(base_url=base, api_key=key)
    return Ark(
        base_url=base, api_key=key, http_client=layer_sync_client(httpx.Client(follow_redirects=True), transport_layer)
    )
