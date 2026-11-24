"""内置清单全部图片模型的计价维度护栏，执行走真实通道，供应商响应由传输层替代。"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import PROVIDER_SPEC_REGISTRY, builtin_model_spec
from lib.backends.gemini_shared import RateLimiter
from lib.backends.image_backends.base import ImageGenerationRequest, ImageRequestFacts, ReferenceImage
from lib.backends.image_usage import estimate_builtin_image_usage
from lib.catalog import query as catalog
from tests.fakes import bounded_poll_clock, patched_genai_client
from tests.http_capture import capture_http

_PROVIDERS = sorted(provider for provider, media in PROVIDER_SPEC_REGISTRY if media == "image")


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize("with_reference", [False, True])
async def test_every_listed_image_model_reports_all_price_dimensions(
    provider: str, with_reference: bool, tmp_path: Path
):
    """逐模型、逐档位核对预估及 generate 结果；不允许缺维度后静默落到 defaults。"""
    buffer = BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    ref = tmp_path / "ref.png"
    ref.write_bytes(image_bytes)
    encoded = base64.b64encode(image_bytes).decode()
    sdk = SimpleNamespace(
        image=SimpleNamespace(
            sample=AsyncMock(
                return_value=SimpleNamespace(
                    respect_moderation=True,
                    url="https://cdn.test/image.png",
                )
            )
        )
    )
    from google.genai import types

    vertex = tmp_path / "vertex.json"
    vertex.write_text('{"project_id": "test"}')
    gemini = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content=AsyncMock(
                    return_value=types.GenerateContentResponse(
                        candidates=[
                            types.Candidate(
                                content=types.Content(
                                    parts=[
                                        types.Part(inline_data=types.Blob(mime_type="image/png", data=image_bytes)),
                                    ]
                                )
                            )
                        ]
                    ),
                )
            )
        )
    )
    credentials: dict[str, str | None] = {"api_key": "sk-test", "credentials_path": str(vertex)}
    if provider != "grok":
        credentials["base_url"] = "https://api.test"
    models = {name: info for name, info in catalog.list_models(provider).items() if info.media_type == "image"}
    assert models
    for model, info in models.items():
        entry = catalog.provider_model_entry(provider, model, "image")
        assert entry is not None
        spec = builtin_model_spec(provider, "image", model)
        sizes = info.resolutions or ["1K"]
        for size in ["1K" if "1K" in sizes else sizes[0]] if with_reference else sizes:
            facts = ImageRequestFacts(
                model=model, aspect_ratio="1:1", image_size=size, reference_count=int(with_reference)
            )
            estimate = estimate_builtin_image_usage(provider, facts)
            with (
                patch("xai_sdk.AsyncClient", return_value=sdk),
                patched_genai_client(gemini),
                patch("google.oauth2.service_account.Credentials.from_service_account_file"),
                capture_http() as router,
                bounded_poll_clock(),
            ):
                if provider == "dashscope":
                    response = {
                        "output": {"choices": [{"message": {"content": [{"image": "https://cdn.test/image.png"}]}}]}
                    }
                elif provider == "minimax":
                    response = {"data": {"image_base64": [encoded]}, "base_resp": {"status_code": 0}}
                elif provider == "vidu":
                    response = {
                        "task_id": "t-1",
                        "state": "success",
                        "creations": [{"url": "https://cdn.test/image.png"}],
                    }
                elif provider == "kling":
                    response = {
                        "code": 0,
                        "data": {
                            "task_id": "t-1",
                            "task_status": "succeed",
                            "task_result": {
                                "images": [{"url": "https://cdn.test/image.png"}],
                            },
                        },
                    }
                else:
                    response = {"data": [{"b64_json": encoded}], "usage": {"output_tokens": 10}}
                router.post(url__regex=r"https://.*").respond(200, json=response)
                router.get("https://cdn.test/image.png").respond(200, content=image_bytes)
                router.get(url__regex=r"https://.*").respond(200, json=response)
                backend = spec.build_backend(
                    LoadedConfig(
                        credentials=credentials,
                        provider_meta=None,
                        rate_limiter=RateLimiter(request_gap=0),
                    ),
                    model,
                )
                result = await backend.generate(
                    ImageGenerationRequest(
                        prompt="cat",
                        output_path=tmp_path / "out.png",
                        aspect_ratio="1:1",
                        image_size=size,
                        reference_images=[ReferenceImage(path=str(ref))] if with_reference else [],
                    )
                )
            assert result.usage is not None, (provider, model)
            assert entry.price is not None, (provider, model)
            for usage in (estimate, result.usage):
                for meter in entry.price.meters:
                    for dimension in meter.dims:
                        assert usage.dimensions.get(dimension) is not None, (
                            provider,
                            model,
                            spec.endpoint,
                            size,
                            dimension,
                            usage,
                        )
