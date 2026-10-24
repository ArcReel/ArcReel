"""OpenRouterImageBackend 单元测试（respx 拦 transport，不打真实 HTTP）。

尺寸参数契约按 supported_parameters 自适应，测试分三组覆盖：
- 模型未收录 / 目录失败 → 退回 OpenAI 同款 size；
- 声明 resolution（gemini 系）→ aspect_ratio + resolution，绝不带 size（互斥 400）；
- 只声明 aspect_ratio（gpt-image 系）→ size + aspect_ratio（+ quality）。
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from PIL import Image

from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageOutputFormatError,
    ImageOutputMissingError,
    ReferenceImage,
)
from lib.backends.image_backends.openrouter import OpenRouterImageBackend
from lib.backends.providers import PROVIDER_OPENROUTER
from tests.http_capture import capture_http, only_request, request_json

# 1x1 PNG 头部若干字节，够 save_image_from_response_item 写盘断言用
_PNG_1PX = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
).decode("ascii")

_BASE_URL = "https://openrouter.ai/api/v1"
_IMAGES_URL = f"{_BASE_URL}/images"
_MODELS_URL = f"{_BASE_URL}/images/models"


def _ok_response() -> httpx.Response:
    return httpx.Response(200, json={"created": 1, "data": [{"b64_json": _PNG_1PX, "media_type": "image/png"}]})


def _models_response(*models: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": list(models)})


def _enum(name: str, *values: str) -> dict:
    return {name: {"type": "enum", "values": list(values)}}


# 与 OpenRouter 目录返回同构的 supported_parameters 样本
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
    assert backend.name == PROVIDER_OPENROUTER


def _limit(model: str, *catalog: dict, status: int = 200) -> int:
    backend = OpenRouterImageBackend(api_key="sk-or-test", model=model, base_url=_BASE_URL)
    with capture_http() as router:
        response = _models_response(*catalog) if status == 200 else httpx.Response(status)
        router.get(_MODELS_URL).mock(return_value=response)
        return asyncio.run(backend.reference_image_limit())


def _ref_range(model_id: str, low: int, high: int) -> dict:
    return {"id": model_id, "supported_parameters": {"input_references": {"type": "range", "min": low, "max": high}}}


@pytest.mark.parametrize(
    ("catalog", "status", "expected"),
    [
        pytest.param((_ref_range("m", 0, 3),), 200, 3, id="declared-below-endpoint-cap"),
        pytest.param((_ref_range("m", 0, 20),), 200, 16, id="declared-above-endpoint-cap"),
        pytest.param((), 200, 16, id="model-not-in-catalog"),
        pytest.param((), 500, 16, id="catalog-unavailable"),
        # 收录却没声明参考图：不报 0（0 = 不裁剪），由 generate 按不支持图生图报错
        pytest.param(({"id": "m", "supported_parameters": {}},), 200, 16, id="no-input-references"),
    ],
)
def test_reference_image_limit_follows_the_model_declaration(catalog, status, expected):
    """上限按模型在目录里声明的 input_references 给出，封顶端点的 16 张。"""
    assert _limit("m", *catalog, status=status) == expected


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
    # 没有声明依据的参数不带：quality 也不发（1K 档在 OpenAI 口径下本会映射出 quality）
    assert "quality" not in req_json
    # 纯 T2I 不带参考图字段
    assert "input_references" not in req_json


def test_discovery_failure_falls_back_to_size(tmp_path: Path):
    """目录查询失败只影响参数精化：生成按 size 兜底照常走，不中断。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=httpx.Response(500))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path))
    req_json = request_json(only_request(route))
    assert set(req_json) == {"model", "prompt", "n", "size"}


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


def test_seed_sent_only_when_declared_or_model_unlisted(tmp_path: Path):
    """seed 只发给声明了它的模型；目录未收录该模型时照常带上。"""
    seeded = {"id": "vendor/seeded", "supported_parameters": {"seed": {"type": "boolean"}}}
    unseeded = {"id": "vendor/unseeded", "supported_parameters": {}}
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(seeded, unseeded))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        for model in ("vendor/seeded", "vendor/unseeded", "vendor/unlisted"):
            req = _request(tmp_path)
            req.seed = 42
            _run(req, model=model)
    sent = [request_json(call.request).get("seed") for call in route.calls]
    assert sent == [42, None, 42]


def test_model_declaring_both_size_and_resolution_takes_resolution(tmp_path: Path):
    """size 与 resolution 同时声明时走 resolution 通道：两者同传会被 400 拒掉，size 不下发。"""
    model = {
        "id": "vendor/both-sizing",
        "supported_parameters": {
            **_enum("size", "1024x1024", "1024x1792"),
            **_enum("resolution", "1K", "2K"),
            **_enum("aspect_ratio", "1:1", "9:16"),
        },
    }
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, image_size="1K"), model="vendor/both-sizing")
    req_json = request_json(only_request(route))
    assert req_json["resolution"] == "1K"
    assert req_json["aspect_ratio"] == "9:16"
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


def test_size_computed_from_selected_ratio_not_request_ratio(tmp_path: Path):
    """请求比例不在枚举时：size 必须按「选中档」算，与 aspect_ratio 同源不自相矛盾。

    请求 2:3、枚举只有 1:1 / 9:16 / 21:9 → 选中 9:16，size 也须按 9:16 算（1008x1792），
    不能按 2:3 算出 688x1024 再配上 aspect_ratio=9:16——两个参数互相矛盾。
    """
    model = {
        "id": "openai/gpt-image-2",
        "supported_parameters": {
            **_enum("aspect_ratio", "1:1", "9:16", "21:9"),
            **_enum("quality", "auto", "low", "medium", "high"),
        },
    }
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, aspect_ratio="2:3", image_size="1K"))
    req_json = request_json(only_request(route))
    assert req_json["aspect_ratio"] == "9:16"
    assert req_json["size"] == "1008x1792"


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


def test_models_query_cached_on_the_backend_instance(tmp_path: Path):
    """目录结果缓存在 backend 实例上：同一实例两次生成只查一次；按新配置构造的实例重新查询。"""
    backend = OpenRouterImageBackend(api_key="sk-or-test", model="openai/gpt-image-2", base_url=_BASE_URL)
    with capture_http() as router:
        models_route = router.get(_MODELS_URL).mock(return_value=_models_response(_GPT_IMAGE_LIKE))
        router.post(_IMAGES_URL).mock(return_value=_ok_response())
        asyncio.run(backend.generate(_request(tmp_path)))
        asyncio.run(backend.generate(_request(tmp_path)))
        assert models_route.call_count == 1
        _run(_request(tmp_path))
    assert models_route.call_count == 2


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
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        with pytest.raises(ImageCapabilityError) as ei:
            _run(_request(tmp_path, refs=[ghost]))
    assert ei.value.code == "image_reference_images_unreadable"
    assert not route.called


def test_partial_missing_refs_fail_loud_without_submitting(tmp_path: Path):
    """多张参考图中一张缺失：跳过会让后续图前移、「图N」错指，须报错且不发出 /images 请求。"""
    refs = [
        _make_ref(tmp_path, "a.png"),
        ReferenceImage(path=str(tmp_path / "ghost.png")),
        _make_ref(tmp_path, "c.png"),
    ]
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        with pytest.raises(ImageCapabilityError) as ei:
            _run(_request(tmp_path, refs=refs))
    assert ei.value.code == "image_reference_images_unreadable"
    assert "ghost.png" in str(ei.value.params.get("names", ""))
    assert not route.called


def test_refs_over_declared_max_rejected_without_submitting(tmp_path: Path):
    """参考图多于模型上限（编排层按另一份目录读数编了号）：报错且不发 /images，不截断后照常计费。"""
    refs = [_make_ref(tmp_path, f"ref-{i}.png") for i in range(5)]
    model = _ref_range("x-ai/grok-imagine-image-2.0", 0, 3)
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        with pytest.raises(ImageCapabilityError) as ei:
            _run(_request(tmp_path, refs=refs), model="x-ai/grok-imagine-image-2.0")
    assert ei.value.code == "image_reference_images_exceeded"
    assert ei.value.params == {"model": "x-ai/grok-imagine-image-2.0", "limit": 3, "count": 5}
    assert not route.called


def test_edit_only_model_rejects_text_to_image(tmp_path: Path):
    """模型声明 input_references 下限 1（只做改图）却没带参考图：报不支持文生图，不发 /images 请求。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(_ref_range("krea/k3-edit", 1, 8)))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        with pytest.raises(ImageCapabilityError) as ei:
            _run(_request(tmp_path), model="krea/k3-edit")
    assert ei.value.code == "image_endpoint_mismatch_no_t2i"
    assert not route.called


def test_fractional_resolution_tier_is_the_smallest_non_downgrading(tmp_path: Path):
    """1.5K 这类小数档也认得：2K 请求（短边 1440）选 1.5K，而不是跳到 2K。"""
    model = {
        "id": "tencent/hy-image",
        "supported_parameters": {**_enum("resolution", "1K", "1.5K", "2K", "4K"), **_enum("aspect_ratio", "9:16")},
    }
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, image_size="2K"), model="tencent/hy-image")
    assert request_json(only_request(route))["resolution"] == "1.5K"


def test_catalog_failure_is_not_requeried_within_the_failure_window(tmp_path: Path):
    """目录查询失败短暂缓存：窗口内的下一次生成不再查目录，窗口过后再查。"""
    now = [1000.0]
    backend = OpenRouterImageBackend(
        api_key="sk-or-test", model="openai/gpt-image-2", base_url=_BASE_URL, clock=lambda: now[0]
    )
    with capture_http() as router:
        models_route = router.get(_MODELS_URL).mock(return_value=httpx.Response(503))
        router.post(_IMAGES_URL).mock(return_value=_ok_response())
        asyncio.run(backend.generate(_request(tmp_path)))
        now[0] += 59
        asyncio.run(backend.generate(_request(tmp_path)))
        assert models_route.call_count == 1
        now[0] += 2
        asyncio.run(backend.generate(_request(tmp_path)))
    assert models_route.call_count == 2


def test_concurrent_first_lookups_query_the_catalog_once():
    """同一实例上并发的首次查询串行化：只查一次目录，各方拿到同一份结果。"""
    backend = OpenRouterImageBackend(api_key="sk-or-test", model="m", base_url=_BASE_URL)

    async def both() -> list[int]:
        return list(await asyncio.gather(backend.reference_image_limit(), backend.reference_image_limit()))

    with capture_http() as router:
        models_route = router.get(_MODELS_URL).mock(return_value=_models_response(_ref_range("m", 0, 3)))
        limits = asyncio.run(both())
    assert limits == [3, 3]
    assert models_route.call_count == 1


def _image_bytes(fmt: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format=fmt)
    return buffer.getvalue()


def test_non_png_raster_output_is_stored_as_png(tmp_path: Path):
    """模型返回 JPEG：按 PNG 落盘，产物格式与固定的 .png 路径一致。"""
    jpeg = base64.b64encode(_image_bytes("JPEG")).decode("ascii")
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        router.post(_IMAGES_URL).mock(
            return_value=httpx.Response(200, json={"data": [{"b64_json": jpeg, "media_type": "image/jpeg"}]})
        )
        _run(_request(tmp_path))
    with Image.open(tmp_path / "out.png") as stored:
        assert stored.format == "PNG"


def test_url_output_is_downloaded_and_stored_as_png(tmp_path: Path):
    """响应只带产物地址：下载后同样转成 PNG 落盘。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        router.post(_IMAGES_URL).mock(
            return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/out.jpg"}]})
        )
        router.get("https://cdn.example.com/out.jpg").mock(
            return_value=httpx.Response(200, content=_image_bytes("JPEG"))
        )
        _run(_request(tmp_path))
    with Image.open(tmp_path / "out.png") as stored:
        assert stored.format == "PNG"


def test_vector_output_raises_structured_failure(tmp_path: Path):
    """模型返回 SVG：转不成位图，报带稳定 code 的错误，不留错标格式的产物。"""
    svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"/>').decode("ascii")
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        router.post(_IMAGES_URL).mock(
            return_value=httpx.Response(200, json={"data": [{"b64_json": svg, "media_type": "image/svg+xml"}]})
        )
        with pytest.raises(ImageOutputFormatError) as ei:
            _run(_request(tmp_path))
    assert ei.value.code == "image_output_format_unsupported"
    assert ei.value.params == {"model": "openai/gpt-image-2", "media_type": "image/svg+xml"}
    assert not (tmp_path / "out.png").exists()


def test_vector_only_model_rejected_before_the_billed_request(tmp_path: Path):
    """目录声明只出 SVG：产物注定转不成 PNG，不发出计费的生成请求。"""
    model = {"id": "recraft/recraft-v4-vector", "supported_parameters": {**_enum("output_format", "svg")}}
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        with pytest.raises(ImageOutputFormatError) as ei:
            _run(_request(tmp_path), model="recraft/recraft-v4-vector")
    assert ei.value.params == {"model": "recraft/recraft-v4-vector", "media_type": "svg"}
    assert not route.called


def test_png_output_format_requested_when_declared(tmp_path: Path):
    model = {"id": "vendor/fmt", "supported_parameters": {**_enum("output_format", "png", "jpeg", "webp")}}
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path), model="vendor/fmt")
    assert request_json(only_request(route))["output_format"] == "png"


def test_path_overrides_change_the_urls_actually_sent(tmp_path: Path):
    """路径覆盖（docs/adr/0101）：生成与目录查询都按覆盖路径接在供应商填写的 base_url 之后发出。"""
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    provider = SimpleNamespace(provider_id="custom-or", base_url="https://relay.example/api/v1", api_key="sk")
    backend = ENDPOINT_REGISTRY["openrouter-images"].build_backend(
        cast("Any", provider),
        "openai/gpt-image-2",
        {"generate": "/v2/generate", "model_catalog": "/v2/catalog"},
    )
    with capture_http() as router:
        catalog = router.get("https://relay.example/api/v1/v2/catalog").mock(return_value=_models_response())
        generate = router.post("https://relay.example/api/v1/v2/generate").mock(return_value=_ok_response())
        asyncio.run(backend.generate(_request(tmp_path)))
    assert catalog.call_count == 1
    assert generate.call_count == 1


def test_refs_within_declared_max_all_sent(tmp_path: Path):
    refs = [_make_ref(tmp_path, f"ref-{i}.png") for i in range(3)]
    model = {
        "id": "x-ai/grok-imagine-image-2.0",
        "supported_parameters": {"input_references": {"type": "range", "min": 0, "max": 3}},
    }
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, refs=refs), model="x-ai/grok-imagine-image-2.0")
    assert len(request_json(only_request(route))["input_references"]) == 3


def test_n_sent_only_when_declared_or_model_unlisted(tmp_path: Path):
    """n 与 seed 同口径：收录却未声明不下发，声明了或未收录照常带 n=1。"""
    with_n = {"id": "vendor/with-n", "supported_parameters": {"n": {"type": "range", "min": 1, "max": 4}}}
    without_n = {"id": "vendor/without-n", "supported_parameters": {}}
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(with_n, without_n))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        for model in ("vendor/with-n", "vendor/without-n", "vendor/unlisted"):
            _run(_request(tmp_path), model=model)
    assert [request_json(call.request).get("n") for call in route.calls] == [1, None, 1]


def test_declared_size_enum_picks_closest_declared_value(tmp_path: Path):
    """声明 WxH 枚举：选比例最接近、短边不低于请求档位的那一档，不合成枚举外的值。"""
    model = {
        "id": "vendor/size-enum",
        "supported_parameters": {**_enum("size", "1024x1024", "1024x1792", "1792x1024", "auto")},
    }
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, aspect_ratio="9:16", image_size="1K"), model="vendor/size-enum")
    assert request_json(only_request(route))["size"] == "1024x1792"


def test_size_enum_with_only_auto_sends_auto(tmp_path: Path):
    """size 枚举只有 auto：发声明的 auto，不合成枚举外的 WxH。"""
    model = {"id": "vendor/size-auto", "supported_parameters": {**_enum("size", "auto")}}
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        _run(_request(tmp_path, aspect_ratio="9:16", image_size="1K"), model="vendor/size-auto")
    assert request_json(only_request(route))["size"] == "auto"


def test_listed_model_without_input_references_rejects_i2i(tmp_path: Path):
    """目录收录了该模型却没声明 input_references：带参考图即报不支持图生图，不发 /images 请求。"""
    model = {"id": "vendor/t2i-only", "supported_parameters": {**_enum("aspect_ratio", "1:1", "9:16")}}
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response(model))
        route = router.post(_IMAGES_URL).mock(return_value=_ok_response())
        with pytest.raises(ImageCapabilityError) as ei:
            _run(_request(tmp_path, refs=[_make_ref(tmp_path)]), model="vendor/t2i-only")
    assert ei.value.code == "image_endpoint_mismatch_no_i2i"
    assert not route.called


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


@pytest.mark.parametrize(
    "body", [b'{"data": []}', b"null", b'{"data": [{}]}'], ids=["empty-data", "null-body", "no-image"]
)
def test_response_without_image_raises_structured_failure(tmp_path: Path, body):
    """成功响应没带回图片：抛带稳定 code 的错误，文案由读侧按语言渲染。"""
    with capture_http() as router:
        router.get(_MODELS_URL).mock(return_value=_models_response())
        router.post(_IMAGES_URL).mock(
            return_value=httpx.Response(200, content=body, headers={"Content-Type": "application/json"})
        )
        with pytest.raises(ImageOutputMissingError) as ei:
            _run(_request(tmp_path))
    assert ei.value.code == "image_output_missing"
    assert ei.value.params == {"model": "openai/gpt-image-2"}
