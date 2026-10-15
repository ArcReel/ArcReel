"""内置供应商获取模型：按供应商取模型列表接口的地址与形态，只取模型 ID，按清单中的 API 模型名精确匹配。"""

from __future__ import annotations

import pytest

from lib.catalog import builtin_discovery
from tests.http_capture import capture_http, only_request


def _openai_list(*model_ids: str) -> dict:
    return {"object": "list", "data": [{"id": m, "object": "model", "created": 0, "owned_by": "x"} for m in model_ids]}


@pytest.mark.parametrize(
    ("provider_id", "base_url", "expected_url"),
    [
        ("openai", None, "https://api.openai.com/v1/models"),
        ("openai", "https://relay.example.com/v1", "https://relay.example.com/v1/models"),
        ("grok", None, "https://api.x.ai/v1/models"),
        ("dashscope", None, "https://dashscope.aliyuncs.com/compatible-mode/v1/models"),
        ("minimax", None, "https://api.minimaxi.com/v1/models"),
        ("agnes", None, "https://apihub.agnes-ai.com/v1/models"),
    ],
)
def test_openai_form_lists_ids_from_provider_base_url(provider_id: str, base_url: str | None, expected_url: str):
    config = {"api_key": "sk-test", **({"base_url": base_url} if base_url else {})}
    with capture_http() as http:
        route = http.get(expected_url).respond(json=_openai_list("b-model", "a-model"))
        ids = builtin_discovery.list_model_ids(provider_id, config)

    assert only_request(route).headers["authorization"] == "Bearer sk-test"
    assert sorted(ids) == ["a-model", "b-model"]


def test_google_form_strips_path_prefix():
    with capture_http() as http:
        route = http.get(url__startswith="https://generativelanguage.googleapis.com/").respond(
            json={"models": [{"name": "models/gemini-3-flash-preview"}, {"name": "models/veo-3.1-generate-preview"}]}
        )
        ids = builtin_discovery.list_model_ids("gemini-aistudio", {"api_key": "g-key"})

    assert only_request(route).headers["x-goog-api-key"] == "g-key"
    assert ids == ["gemini-3-flash-preview", "veo-3.1-generate-preview"]


def test_classify_lists_amphibious_listing_once_per_model_key_and_infers_unknown_media():
    rows = builtin_discovery.classify_model_ids("kling", ["kling-v3-omni", "acme-video-x"])

    assert [(r.model_id, r.recognized, r.model_key, r.media_type) for r in rows] == [
        ("acme-video-x", False, None, "video"),
        ("kling-v3-omni", True, "kling-v3-omni-image", "image"),
        ("kling-v3-omni", True, "kling-v3-omni", "video"),
    ]
