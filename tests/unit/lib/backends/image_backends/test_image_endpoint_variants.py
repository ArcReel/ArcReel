"""图片请求与计量由所选端点决定，型号名不参与。"""

from pathlib import Path

import pytest

from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import get_provider_spec
from lib.backends.image_backends.base import ImageGenerationRequest, ImageRequestFacts
from lib.backends.image_backends.registry import channel_usage_estimator
from tests.http_capture import capture_http, only_request, request_json


@pytest.mark.parametrize(
    ("provider", "endpoint", "size", "resolution", "ratio"),
    [
        ("ark", "ark-seedream-3", "720x1280", "720x1280", None),
        ("ark", "ark-seedream-4", "1600x2848", "1600x2848", None),
        ("dashscope", "dashscope-qwen-image", "1440*2560", "1440*2560", None),
        ("dashscope", "dashscope-wan-image", "1440*2560", "1440*2560", None),
        ("dashscope", "dashscope-qwen-image-edit", "1152*2048", "1152*2048", None),
        ("agnes", "agnes-image-pixels", "1152x2048", "2K", None),
        ("agnes", "agnes-image-tier", "1K", "1K", "9:16"),
    ],
)
async def test_endpoint_controls_request_and_metering_when_model_name_changes(
    tmp_path: Path, provider: str, endpoint: str, size: str, resolution: str, ratio: str | None
):
    spec = get_provider_spec(provider, "image", endpoint)
    for model in ("opaque-model", "wan-pro-seedream-3-agnes-image-2.5"):
        backend = spec.build_backend(
            LoadedConfig(
                credentials={"api_key": "sk-test", "base_url": "https://api.test"},
                provider_meta=None,
                rate_limiter=None,
            ),
            model,
        )
        response = (
            {"output": {"choices": [{"message": {"content": [{"image": "https://cdn.test/image.png"}]}}]}}
            if provider == "dashscope"
            else {"data": [{"b64_json": "aW1hZ2U="}]}
        )
        with capture_http() as router:
            route = router.post(url__regex=r"https://api.test/.*").respond(200, json=response)
            router.get("https://cdn.test/image.png").respond(200, content=b"image")
            result = await backend.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "out.png"))
        body = request_json(only_request(route))
        assert (body.get("parameters") or body)["size"] == size
        assert body.get("ratio") == ratio
        assert result.usage is not None
        assert result.usage.dimensions["resolution"] == resolution
        assert result.usage.quantities["output_images"] == 1
        if provider != "dashscope":
            assert (
                channel_usage_estimator(spec.registry_backend)(
                    ImageRequestFacts(model=model, aspect_ratio="9:16")
                ).dimensions["resolution"]
                == resolution
            )
