"""
Grok (xAI) shared utilities module

Reused by text_backends / image_backends / video_backends.

Contains:
- create_grok_client — xAI AsyncClient factory
"""

from __future__ import annotations


def create_grok_client(*, api_key: str | None = None):
    """Create an xAI AsyncClient, validating and constructing uniformly."""
    import xai_sdk

    if not api_key:
        raise ValueError("XAI_API_KEY is not set\nPlease configure the xAI API Key in the system settings page")
    return xai_sdk.AsyncClient(api_key=api_key)
