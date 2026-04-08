"""URL normalisation utility functions."""

from __future__ import annotations

import re


def ensure_openai_base_url(url: str | None) -> str | None:
    """Automatically append the /v1 path suffix for OpenAI-compatible APIs.

    Users may only enter ``https://api.example.com``, but the OpenAI SDK expects
    ``https://api.example.com/v1``. This function appends it when the version path is missing.
    """
    if not url:
        return url
    stripped = url.strip().rstrip("/")
    if not re.search(r"/v\d+$", stripped):
        stripped += "/v1"
    return stripped


def normalize_base_url(url: str | None) -> str | None:
    """Ensure base_url ends with a trailing slash.

    The Google genai SDK's http_options.base_url requires a trailing /,
    otherwise request path concatenation will fail. Used by the preset Gemini backend.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.endswith("/"):
        url += "/"
    return url


def ensure_google_base_url(url: str | None) -> str | None:
    """Normalise base_url for the Google genai SDK.

    The Google genai SDK automatically appends ``api_version`` (default ``v1beta``) after base_url.
    If the user mistakenly enters ``https://example.com/v1beta``, the SDK will produce
    ``https://example.com/v1beta/v1beta/models``, causing requests to fail.

    This function strips any trailing version path (e.g. ``/v1beta``, ``/v1``) and ensures a trailing ``/``.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    url = url.rstrip("/")
    # Strip trailing version path (/v1, /v1beta, /v1alpha, etc.)
    url = re.sub(r"/v\d+\w*$", "", url)
    if not url.endswith("/"):
        url += "/"
    return url
