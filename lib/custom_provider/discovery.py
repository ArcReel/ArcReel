"""Custom discovery: select SDKs by discovery_format, return endpoints."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from google import genai
from openai import OpenAI

from arcreel_catalog_core import MEDIA_TYPES, CatalogEntry
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.catalog import query as catalog
from lib.config.anthropic_probe import anthropic_auth_headers
from lib.config.url_utils import anthropic_endpoint_url, validate_anthropic_base_url
from lib.custom_provider.endpoints import endpoint_to_media_type, infer_endpoint, infer_media_type
from lib.custom_provider.model_facts import matched_entry_summary

logger = logging.getLogger(__name__)

#: Anthropic model-list I/O timeout.
_DISCOVERY_TIMEOUT_SECONDS = 15.0


class UnsupportedDiscoveryFormatError(ValueError):
    """Unsupported discovery_format, distinct from credential/network ValueError during SDK calls."""


async def discover_models(
    *,
    discovery_format: str,
    base_url: str | None,
    api_key: str,
) -> list[dict]:
    """Query available provider models, annotating endpoints.

    Returns:
        list of dict: model_id, display_name, endpoint, is_default, is_enabled
    """
    if discovery_format == "openai":
        return await _discover_openai(base_url, api_key)
    if discovery_format == "google":
        return await _discover_google(base_url, api_key)
    if discovery_format == "anthropic":
        return await _discover_anthropic(base_url, api_key)
    raise UnsupportedDiscoveryFormatError(
        f"不支持的 discovery_format: {discovery_format!r}，支持: 'openai', 'google', 'anthropic'"
    )


async def _discover_openai(base_url: str | None, api_key: str) -> list[dict]:
    def _sync():
        from lib.config.url_utils import ensure_openai_base_url

        client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
        raw_models = client.models.list()
        models = sorted(raw_models, key=lambda m: m.id)
        return _build_result_list([m.id for m in models], "openai")

    return await asyncio.to_thread(_sync)


async def _discover_google(base_url: str | None, api_key: str) -> list[dict]:
    def _sync():
        from lib.config.url_utils import ensure_google_base_url

        kwargs: dict = {"api_key": api_key}
        effective_url = ensure_google_base_url(base_url) if base_url else None
        if effective_url:
            kwargs["http_options"] = {"base_url": effective_url}
        client = genai.Client(**kwargs)
        raw_models = client.models.list()

        model_ids: list[str] = []
        output_limits: dict[str, int] = {}
        for m in raw_models:
            if not m.name:
                continue
            model_id: str = m.name
            if model_id.startswith("models/"):
                model_id = model_id[len("models/") :]
            model_ids.append(model_id)
            limit = getattr(m, "output_token_limit", None)
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                output_limits[model_id] = limit

        model_ids.sort()
        return _build_result_list(model_ids, "google", output_limits=output_limits)

    return await asyncio.to_thread(_sync)


async def _discover_anthropic(base_url: str | None, api_key: str) -> list[dict]:
    """Discover Anthropic models via GET {base_url}/v1/models.

    base_url equals Agent call addresses, retaining subpaths: gateways mounting Claude at
    /anthropic also expose lists beneath that subpath.

    ``artifact_http_client`` applies shared destination gates: reject link-local/cloud metadata,
    permit loopback/private networks for self-hosted gateways. Host resolution lies outside client I/O budgets,
    adding at most ``DNS_RESOLVE_TIMEOUT_SECONDS`` when DNS cannot resolve.

    Dicts match OpenAI/Google shapes but endpoint remains empty:
    anthropic bypasses ENDPOINT_REGISTRY dispatch, frontend reads model_id only.

    Raises:
        InvalidAnthropicBaseUrlError: Query/fragment/userinfo in base_url, or nonabsolute/non-http(s) addresses.
        ArtifactDestinationRejectedError: Disallowed schemes/resolved addresses.
    """
    normalized = validate_anthropic_base_url(base_url or "https://api.anthropic.com")
    url = anthropic_endpoint_url(normalized, "/v1/models")
    async with artifact_http_client(timeout=_DISCOVERY_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, headers=anthropic_auth_headers(api_key))
        if resp.status_code == 401:
            # Some gateways (Volcengine Ark) accept only Authorization: Bearer, returning 401 for x-api-key.
            resp = await client.get(url, headers=anthropic_auth_headers(api_key, bearer=True))
    raise_for_status_redacted(resp)
    data = resp.json()
    entries = sorted(
        (m for m in data.get("data", []) if m.get("id")),
        key=lambda m: m["id"],
    )
    return [
        {
            "model_id": m["id"],
            "display_name": m.get("display_name") or m["id"],
            "endpoint": "",
            "is_default": False,
            "is_enabled": True,
        }
        for m in entries
    ]


def match_discovered_model(
    model_id: str, discovery_format: str, media_type: str | None = None
) -> tuple[CatalogEntry | None, str]:
    """Matching entries/default endpoints for discovered IDs (docs/adr/0105).

    Without endpoints, first match entries unrestricted by media; dual-modality ambiguity retries within ``media_type``, otherwise name-inferred
    media. Entry matches select endpoints within entry media; unmatched names infer both media/endpoints.
    """
    entry = catalog.find_entry(model_id) or catalog.find_entry(
        model_id, media_type=media_type or infer_media_type(model_id, discovery_format)
    )
    if entry is None:
        return None, infer_endpoint(model_id, discovery_format)
    return entry, infer_endpoint(model_id, discovery_format, entry.media_type)


def _build_result_list(
    model_ids: list[str], discovery_format: str, *, output_limits: Mapping[str, int] | None = None
) -> list[dict]:
    """Add matching entry summaries/default endpoints; first model per media defaults.

    ``output_limits`` contains protocol-reported maximum outputs for text only, None when absent.
    ``endpoint_by_media_type`` maps defaults for switching entry media in discovery dialogs
    after users select matching models.
    """
    seen_media: set[str] = set()
    result: list[dict] = []
    for model_id in model_ids:
        entry, endpoint = match_discovered_model(model_id, discovery_format)
        media = endpoint_to_media_type(endpoint)
        is_default = media not in seen_media
        seen_media.add(media)
        result.append(
            {
                "model_id": model_id,
                "display_name": model_id,
                "endpoint": endpoint,
                "media_type": media,
                "is_default": is_default,
                "is_enabled": True,
                "max_output_tokens": (output_limits or {}).get(model_id) if media == "text" else None,
                # Unmatched means None, unpriced after addition.
                "matched_entry": matched_entry_summary(model_id, media) if entry is not None else None,
                "recognized": entry is not None,
                "endpoint_by_media_type": {m: infer_endpoint(model_id, discovery_format, m) for m in MEDIA_TYPES},
            }
        )
    return result
