"""自定义供应商模型发现（按 discovery_format 选 SDK；返回 endpoint）。"""

from __future__ import annotations

import asyncio
import logging

from google import genai
from openai import OpenAI

from lib.config.url_utils import ensure_anthropic_base_url
from lib.custom_provider.endpoints import endpoint_to_media_type, infer_endpoint
from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)


async def discover_models(
    *,
    discovery_format: str,
    base_url: str | None,
    api_key: str,
) -> list[dict]:
    """查询供应商可用模型列表，每项标注 endpoint。

    Returns:
        list of dict: model_id, display_name, endpoint, is_default, is_enabled
    """
    if discovery_format == "openai":
        return await _discover_openai(base_url, api_key)
    elif discovery_format == "google":
        return await _discover_google(base_url, api_key)
    elif discovery_format == "anthropic":
        return await _discover_anthropic(base_url, api_key)
    else:
        raise ValueError(f"不支持的 discovery_format: {discovery_format!r}，支持: 'openai', 'google', 'anthropic'")


async def _discover_openai(base_url: str | None, api_key: str) -> list[dict]:
    def _sync():
        from lib.config.url_utils import ensure_openai_base_url

        client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
        raw_models = client.models.list()
        models = sorted(raw_models, key=lambda m: m.id)
        return _build_result_list([(m.id, infer_endpoint(m.id, "openai")) for m in models])

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

        entries: list[tuple[str, str]] = []
        for m in raw_models:
            model_id = m.name
            if model_id.startswith("models/"):
                model_id = model_id[len("models/") :]
            entries.append((model_id, infer_endpoint(model_id, "google")))

        entries.sort(key=lambda e: e[0])
        return _build_result_list(entries)

    return await asyncio.to_thread(_sync)


async def _discover_anthropic(base_url: str | None, api_key: str) -> list[dict]:
    """Anthropic Messages 协议 GET /v1/models 发现可用模型。

    返回 dict 与 OpenAI/Google 路径同形态，但 endpoint 字段为空字符串
    （anthropic 不参与 ENDPOINT_REGISTRY 派发，前端只读 model_id）。
    """
    normalized = ensure_anthropic_base_url(base_url) or "https://api.anthropic.com"
    resp = await get_http_client().get(
        f"{normalized}/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=15.0,
    )
    resp.raise_for_status()
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


def _build_result_list(entries: list[tuple[str, str]]) -> list[dict]:
    """每个推算 media_type 取首项为 default。"""
    seen_media: set[str] = set()
    result: list[dict] = []
    for model_id, endpoint in entries:
        media = endpoint_to_media_type(endpoint)
        is_default = media not in seen_media
        seen_media.add(media)
        result.append(
            {
                "model_id": model_id,
                "display_name": model_id,
                "endpoint": endpoint,
                "is_default": is_default,
                "is_enabled": True,
            }
        )
    return result
