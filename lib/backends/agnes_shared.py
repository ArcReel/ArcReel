"""Shared Agnes utilities.

Reused by image_backends / video_backends / text_backends / config / connection tests. Agnes provides
OpenAI-style apihub endpoints with one `/v1` base and single-key Bearer authentication:
- AGNES_BASE_URL: default base including `/v1`.
- resolve_agnes_api_key: Bearer key resolution, raising when absent without environment fallback.
- agnes_base_url: normalize {host}/v1, accepting hosts or `/v1` suffixes.
- agnes_host: normalize {host} without `/v1`, for non-OpenAI endpoints under gateway roots.
- agnes_headers: Bearer authentication headers.
"""

from __future__ import annotations

# Default base including /v1; users may configure base_url for self-hosted relays.
AGNES_BASE_URL = "https://apihub.agnes-ai.com/v1"

# Single known suffix, stripped during host normalization to accept complete bases.
_V1_SUFFIX = "/v1"


def resolve_agnes_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Agnes API Key")
    return api_key.strip()


def agnes_host(configured: str | None = None) -> str:
    """Extract hosts from configured base_url by stripping `/v1`, defaulting to the default host.

    Not all gateway endpoints live under `/v1`: final-video queries at `/agnesapi` use host roots,
    requiring this versionless base.
    """
    # Strip before emptiness checks: whitespace-only strings are truthy and bypass or; fall back after stripping,
    # otherwise bases become empty and produce invalid relative URLs such as "/v1".
    base = ((configured or "").strip() or AGNES_BASE_URL).rstrip("/")
    if base.endswith(_V1_SUFFIX):
        return base[: -len(_V1_SUFFIX)]
    return base


def agnes_base_url(configured: str | None = None) -> str:
    """OpenAI-compatible base: {host}/v1."""
    return f"{agnes_host(configured)}{_V1_SUFFIX}"


def agnes_headers(api_key: str) -> dict[str, str]:
    """Bearer authentication headers.

    Reuse resolve_agnes_api_key validation: empty/whitespace keys raise locally, avoiding
    ``Authorization: Bearer `` and delayed upstream 401 failures.
    """
    api_key = resolve_agnes_api_key(api_key)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
