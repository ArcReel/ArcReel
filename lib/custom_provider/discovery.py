"""自定义供应商模型发现（按 discovery_format 选 SDK；返回 endpoint）。"""

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

#: Anthropic 协议模型列表请求的 I/O 超时。
_DISCOVERY_TIMEOUT_SECONDS = 15.0


class UnsupportedDiscoveryFormatError(ValueError):
    """discovery_format 取值不在受支持集合内，与 SDK 调用期的凭证/网络类 ValueError 区分。"""


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
    """Anthropic 协议 GET {base_url}/v1/models 发现可用模型。

    base_url 与 Agent 调用地址同一个值，不剥子路径：网关把 Claude 协议挂在
    /anthropic 等子路径下时，模型列表也在同一子路径下。

    出站目的地经 ``artifact_http_client`` 校验，与产物下载同一道闸：链路本地与云元数据地址
    一律拒绝，环回与私网放行（自建网关合法地跑在其中）。这道校验的主机名解析在 client 的
    I/O 超时预算之外，解析不通时一次请求最多再等 ``DNS_RESOLVE_TIMEOUT_SECONDS``。

    返回 dict 与 OpenAI/Google 路径同形态，但 endpoint 字段为空字符串
    （anthropic 不参与 ENDPOINT_REGISTRY 派发，前端只读 model_id）。

    Raises:
        InvalidAnthropicBaseUrlError: base_url 含 query / fragment / userinfo 或不是绝对 http(s) 地址。
        ArtifactDestinationRejectedError: 目标协议或解析出的地址不在允许范围内。
    """
    normalized = validate_anthropic_base_url(base_url or "https://api.anthropic.com")
    url = anthropic_endpoint_url(normalized, "/v1/models")
    async with artifact_http_client(timeout=_DISCOVERY_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, headers=anthropic_auth_headers(api_key))
        if resp.status_code == 401:
            # 部分网关（火山方舟）只认 Authorization: Bearer，对 x-api-key 一律 401
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


def match_discovered_model(model_id: str, discovery_format: str) -> tuple[CatalogEntry | None, str]:
    """获取到的模型 ID 对应的条目与默认调用端点（docs/adr/0105）。

    还没有调用端点，先不限媒体类型对应条目；两栖模型这样对应不上，再按名字推断的媒体类型取。对应上条目的，
    在条目的媒体类型内选端点；对应不上的，按名字同时推断媒体类型与端点。
    """
    entry = catalog.find_entry(model_id) or catalog.find_entry(
        model_id, media_type=infer_media_type(model_id, discovery_format)
    )
    if entry is None:
        return None, infer_endpoint(model_id, discovery_format)
    return entry, infer_endpoint(model_id, discovery_format, entry.media_type)


def _build_result_list(
    model_ids: list[str], discovery_format: str, *, output_limits: Mapping[str, int] | None = None
) -> list[dict]:
    """每个模型带上对应的条目摘要与默认调用端点；每个媒体类型的首项为 default。

    ``output_limits`` 是协议给出的模型最大输出长度，只对文本模型给出；没有给出时为 None。
    ``endpoint_by_media_type`` 是改为对应其他媒体类型的条目时各媒体类型下的默认端点，供获取模型对话框在
    用户选了「对应模型」后换端点。
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
                # 对应不上为 None，添加后费用记为未定价。
                "matched_entry": matched_entry_summary(model_id, media) if entry is not None else None,
                "recognized": entry is not None,
                "endpoint_by_media_type": {m: infer_endpoint(model_id, discovery_format, m) for m in MEDIA_TYPES},
            }
        )
    return result
