"""自定义供应商模型发现（按 discovery_format 选 SDK；返回 endpoint）。"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Mapping

from google import genai
from openai import OpenAI

from lib.comfyui_workflow import (
    COMFYUI_ENDPOINT,
    ComfyUIWorkflowConfigError,
    comfyui_headers,
    detect_comfyui_endpoint_config,
    normalize_comfyui_base_url,
    workflow_display_name,
    workflow_profile_id,
)
from lib.config.anthropic_url import derive_anthropic_endpoints
from lib.custom_provider.endpoints import endpoint_to_media_type, infer_endpoint
from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)


class UnsupportedDiscoveryFormatError(ValueError):
    """discovery_format 取值不在受支持集合内，与 SDK 调用期的凭证/网络类 ValueError 区分。"""

    pass


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
    elif discovery_format == "comfyui":
        return await _discover_comfyui(base_url, api_key)
    else:
        raise UnsupportedDiscoveryFormatError(
            f"不支持的 discovery_format: {discovery_format!r}，"
            "支持: 'openai', 'google', 'anthropic', 'comfyui'"
        )


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
            if not m.name:
                continue
            model_id: str = m.name
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
    ep = derive_anthropic_endpoints(base_url or "https://api.anthropic.com")
    normalized = ep.discovery_root or "https://api.anthropic.com"
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


def _successful_comfyui_workflow(record: object) -> Mapping[str, object] | None:
    if not isinstance(record, dict):
        return None
    status = record.get("status")
    if isinstance(status, dict):
        status_str = str(status.get("status_str", "")).lower()
        if status_str in {"error", "failed"}:
            return None
        if status.get("completed") is not True and not record.get("outputs"):
            return None
    elif not record.get("outputs"):
        return None
    prompt = record.get("prompt")
    if not isinstance(prompt, list) or len(prompt) < 3 or not isinstance(prompt[2], dict):
        return None
    return prompt[2]


async def _discover_comfyui(base_url: str | None, api_key: str) -> list[dict]:
    """Import reusable video profiles from recent successful ComfyUI history entries."""
    root = normalize_comfyui_base_url(base_url or "")
    headers = comfyui_headers(api_key)
    client = get_http_client()
    history_response, object_info_response = await asyncio.gather(
        client.get(f"{root}/history", params={"max_items": 30}, headers=headers, timeout=30.0),
        client.get(f"{root}/object_info", headers=headers, timeout=30.0),
    )
    history_response.raise_for_status()
    object_info_response.raise_for_status()
    history = history_response.json()
    object_info = object_info_response.json()
    if not isinstance(history, dict):
        raise ComfyUIWorkflowConfigError("ComfyUI history response is not an object")
    if not isinstance(object_info, dict):
        raise ComfyUIWorkflowConfigError("ComfyUI object_info response is not an object")

    result: list[dict] = []
    seen: set[str] = set()
    for record in reversed(list(history.values())):
        workflow = _successful_comfyui_workflow(record)
        if workflow is None:
            continue
        try:
            config = detect_comfyui_endpoint_config(workflow, object_info=object_info)
        except ComfyUIWorkflowConfigError as exc:
            logger.debug("跳过无法映射的 ComfyUI 历史工作流: %s", exc)
            continue
        bindings = config["bindings"]
        # ArcReel's current video lane is image-to-video.  Do not advertise a profile that
        # cannot accept the storyboard frame it will always receive.
        if "start_image" not in bindings:
            continue
        model_id = workflow_profile_id(config)
        if model_id in seen:
            continue
        seen.add(model_id)
        metadata = config.get("metadata")
        duration = metadata.get("duration_default_seconds") if isinstance(metadata, dict) else None
        result.append(
            {
                "model_id": model_id,
                "display_name": workflow_display_name(config),
                "endpoint": COMFYUI_ENDPOINT,
                "is_default": len(result) == 0,
                "is_enabled": True,
                "supported_durations": [duration] if isinstance(duration, int) else None,
                "capability_overrides": {"last_frame": True} if "end_image" in bindings else None,
                "endpoint_config": config,
            }
        )
    duplicate_names = Counter(str(item["display_name"]) for item in result)
    for item in result:
        if duplicate_names[str(item["display_name"])] > 1:
            item["display_name"] = f"{item['display_name']} · {str(item['model_id'])[-4:]}"
    return result


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
