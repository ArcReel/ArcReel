"""OpenRouterImageBackend 单元测试（respx 拦 transport，不打真实 HTTP）。

尺寸参数契约按 supported_parameters 自适应，测试分三组覆盖：
- 模型未收录 / 目录失败 → 退回 OpenAI 同款 size；
- 声明 resolution（gemini 系）→ aspect_ratio + resolution，绝不带 size（互斥 400）；
- 只声明 aspect_ratio（gpt-image 系）→ size + aspect_ratio（+ quality）。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx
import pytest

from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.image_backends.openrouter import OpenRouterImageBackend, _model_params_cache
from lib.backends.providers import PROVIDER_OPENROUTER
from tests.http_capture import capture_http, only_request, request_json

# 1x1 PNG 头部若干字节，够 save_image_from_response_item 写盘断言用
_PNG_1PX = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
).decode("ascii")

_BASE_URL = "https://openrouter.ai/api/v1"
_IMAGES_URL = f"{_BASE_URL}/images"
_MODELS_URL = f"{_BASE_URL}/images/models"


@pytest.fixture(autouse=True)
def _clear_model_params_cache():
    """目录查询是进程级缓存，测试间必须清空，否则用例间声明串味。"""
    _model_params_cache.clear()
    yield
    _model_params_cache.clear()


def _ok_response() -> httpx.Response:
    return httpx.Response(200, json={"created": 1, "data": [{"b64_json": _PNG_1PX, "media_type": "image/png"}]})


def _models_response(*models: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": list(models)})


def _enum(name: str, *values: str) -> dict:
    return {name: {"type": "enum", "values": list(values)}}


# 与 OpenRouter 实测返回同构的 supported_parameters 样本
_GEMINI_LIKE = {
    "id": "google/gemini-3.1-flash-image",
    "supported_parameters": {
        **_enum("resolution", "512", "1K", "2K", "4K"),
        **_enum("aspect_ratio", "1:1", "9:16", "16:9", "21:9"),
        "input_references": {"type": "range", "min": 0, "max": 14},
    },
}
_GPT_IMAGE_LIKE = {
    "id": "openai/gpt-image-2",
    "supported_parameters": {
        **_enum("aspect_ratio", "1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9"),
        **_enum("quality", "auto", "low", "medium", "high"),
        "input_references": {"type": "range", "min": 0, "max": 16},
    },
}
_GROK_LIKE = {
    "id": "x-ai/grok-imagine-image-2.0",
    "supported_parameters": {
        **_enum("resolution", "1K", "2K"),
        **_enum("aspect_ratio", "1:1", "9:16", "16:9"),
        **_enum("quality", "low", "medium"),
        "input_references": {"type": "range", "min": 0, "max": 3},
    },
}


def _make_ref(tmp_path: Path, name: str = "ref.png") -> ReferenceImage:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\nfake-ref-bytes")
    return ReferenceImage(path=str(p))


def _request(
    tmp_path: Path,
    *,
    refs: list[ReferenceImage] | None = None,
    aspect_ratio: str = "9:16",
    image_size: str | None = "1K",
) -> ImageGenerationRequest:
    return ImageGenerationRequest(
        prompt="a red panda astronaut",
        output_path=tmp_path / "out.png",
        reference_images=refs or [],
        aspect_ratio=aspect_ratio,
        image_size=image_size,
    )


def _run(req: ImageGenerationRequest, *, model: str = "openai/gpt-image-2", api_key: str = "sk-or-test"):
    backend = OpenRouterImageBackend(api_key=api_key, model=model, base_url=_BASE_URL)
    return asyncio.run(backend.generate(req))


def test_caps_and_defaults():
    backend = OpenRouterImageBackend(base_url=_BASE_URL)
    assert backend.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
    assert backend.max_reference_images == 16
    assert backend.name == PROVIDER_OPENROUTER


def test_unknown_model_falls_back_to_size(tmp_path: Path):
    """目录不含该模型 → 退回 OpenAI 同款 size（WxH），不带声明式参数。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path))
    req_json = request_json(only_request(route))
    assert req_json["model"] == "openai/gpt-image-2"
    assert req_json["size"] == "1008x1792"  # 1K 档短边 1024、9:16，取比例零偏差的最近对齐解
    assert "resolution" not in req_json
    assert "aspect_ratio" not in req_json
    # 纯 T2I 不带参考图字段
    assert "input_references" not in req_json


def test_discovery_failure_falls_back_to_size(tmp_path: Path):
    """目录查询失败只影响参数精化：生成按 size 兜底照常走，不中断。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=httpx.Response(500))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path))
    req_json = request_json(only_request(route))
    assert req_json["size"]
    assert "resolution" not in req_json


def test_resolution_model_sends_ar_and_resolution_never_size(tmp_path: Path):
    """gemini 系声明 resolution：aspect_ratio + resolution 下发，size 绝不出现（互斥 400）。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(_GEMINI_LIKE))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, image_size="2K"), model="google/gemini-3.1-flash-image")
    req_json = request_json(only_request(route))
    assert req_json["aspect_ratio"] == "9:16"
    # 2K 档短边 1440 → 枚举里 ≥ 请求值的最小档 2K
    assert req_json["resolution"] == "2K"
    assert "size" not in req_json


def test_resolution_model_nearest_ratio_and_omit_low_quality(tmp_path: Path):
    """比例不在声明枚举取数值最近档；grok 系 quality 枚举不含 high，档位映射不上即省略。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(_GROK_LIKE))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, aspect_ratio="2:3", image_size="4K"), model="x-ai/grok-imagine-image-2.0")
    req_json = request_json(only_request(route))
    # 2:3≈0.667：1:1=1.0 差 0.333，9:16≈0.563 差 0.104 → 取 9:16
    assert req_json["aspect_ratio"] == "9:16"
    # 4K 档短边 2160 超出枚举（最高 2K）→ 不降档原则失败后取最大档 2K
    assert req_json["resolution"] == "2K"
    assert "quality" not in req_json
    assert "size" not in req_json


def test_ratio_model_sends_size_ar_and_quality(tmp_path: Path):
    """gpt-image 系只声明 aspect_ratio：size 撑清晰度 + aspect_ratio 锁比例 + quality 叠加。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(_GPT_IMAGE_LIKE))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, image_size="2K"))
    req_json = request_json(only_request(route))
    assert req_json["aspect_ratio"] == "9:16"
    assert req_json["quality"] == "high"
    assert "size" in req_json
    assert "resolution" not in req_json


def test_known_model_without_sizing_declarations_omits_all(tmp_path: Path):
    """收录但 size / aspect_ratio / resolution 一概不声明：省略全部尺寸参数，信任模型默认。"""
    model = {"id": "meta/muse-image", "supported_parameters": {}}
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path), model="meta/muse-image")
    req_json = request_json(only_request(route))
    assert "size" not in req_json
    assert "aspect_ratio" not in req_json
    assert "resolution" not in req_json
    assert "quality" not in req_json


def test_models_query_cached_within_ttl(tmp_path: Path):
    """目录查询进程级缓存：同一 (base_url, model) 两次生成只打一次目录。"""
    with capture_http() as router:
        models_route = router.get(_MODELS_URL).mock(return_value=_models_response(_GPT_IMAGE_LIKE))
        router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path))
        _run(_request(tmp_path))
    assert models_route.call_count == 1


def test_i2i_sends_data_uri_references(tmp_path: Path):
    ref = _make_ref(tmp_path)
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, refs=[ref]))
    req_json = request_json(only_request(route))
    refs_payload = req_json["input_references"]
    assert len(refs_payload) == 1
    assert refs_payload[0]["type"] == "image_url"
    url = refs_payload[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"\x89PNG\r\nfake-ref-bytes"


def test_missing_ref_files_raise_rather_than_silent_t2i(tmp_path: Path):
    ghost = ReferenceImage(path=str(tmp_path / "nope.png"))
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        router.post(_IMAGES_URL).mock(return_value=_ok_response())
        with pytest.raises(ImageCapabilityError):
            _run(_request(tmp_path, refs=[ghost]))


def test_refs_truncated_to_declared_max(tmp_path: Path):
    """模型声明 input_references 上限 3：超限截断告警而非 400 拒单。"""
    refs = [_make_ref(tmp_path, f"ref-{i}.png") for i in range(5)]
    model = {
        "id": "x-ai/grok-imagine-image-2.0",
        "supported_parameters": {"input_references": {"type": "range", "min": 0, "max": 3}},
    }
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, refs=refs), model="x-ai/grok-imagine-image-2.0")
    req_json = request_json(only_request(route))
    assert len(req_json["input_references"]) == 3


def test_zero_ref_model_rejects_i2i_fail_loud(tmp_path: Path):
    """声明 input_references 上限 0 还带参考图：fail-loud 而非静默丢图照常计费。"""
    model = {
        "id": "inclusionai/ming-image-0.1-design",
        "supported_parameters": {"input_references": {"type": "range", "min": 0, "max": 0}},
    }
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        router.post(_IMAGES_URL).mock(return_value=_ok_response())
        with pytest.raises(ImageCapabilityError):
            _run(_request(tmp_path, refs=[_make_ref(tmp_path)]), model="inclusionai/ming-image-0.1-design")


def test_saves_b64_png(tmp_path: Path):
    out = tmp_path / "out.png"
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path))
    assert out.read_bytes().startswith(b"\x89PNG")


def test_auth_header_carries_api_key(tmp_path: Path):
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path))
    assert route.calls
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-or-test"


def test_empty_data_raises_with_model(tmp_path: Path):
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        router.post(_IMAGES_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        with pytest.raises(RuntimeError, match="openai/gpt-image-2"):
            _run(_request(tmp_path))
