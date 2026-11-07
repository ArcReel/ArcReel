"""Custom-provider Ark Seedream image endpoint: construct the channel through the endpoint registry; respx captures actual outbound requests.

Ark has no ``/images/edits``: text-to-image and reference-image generation both use ``/images/generations``, with references in the JSON
``image`` field. Reference-image limits, size derivation, and metering share the built-in Ark backend implementation.
"""

from __future__ import annotations

import base64
from functools import partial
from pathlib import Path

import httpx
import openai
import pytest

from lib.backends.image_backends.ark import ArkImageBackend
from lib.backends.image_backends.base import (
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageRequestFacts,
    ReferenceImage,
)
from lib.backends.image_capabilities import backend_reference_image_limit
from lib.backends.input_limits import InputLimit
from lib.custom_provider.backends import CustomImageBackend
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
from lib.custom_provider.factory import create_custom_backend
from lib.custom_provider.metering import custom_image_estimate
from lib.db.models.custom_provider import CustomProvider
from lib.generation.task_failure import parse_failure, render_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.i18n import _ as translate_message
from tests.factories import make_display_names
from tests.http_capture import capture_http, only_request, request_json


def _provider(base_url: str = "https://relay.example/proxy") -> CustomProvider:
    return CustomProvider(id=7, display_name="relay", discovery_format="openai", base_url=base_url, api_key="sk")


_PROVIDER = _provider()
_MODEL = "doubao-seedream-5-0-260128"
_GENERATIONS = "https://relay.example/proxy/api/v3/images/generations"


def _image_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"model": _MODEL, "created": 1, "data": [{"b64_json": base64.b64encode(b"png").decode()}]},
    )


def _refs(tmp_path: Path, count: int) -> list[ReferenceImage]:
    refs = []
    for i in range(count):
        path = tmp_path / f"ref-{i}.png"
        path.write_bytes(f"img-{i}".encode())
        refs.append(ReferenceImage(path=str(path)))
    return refs


def _data_uri(i: int) -> str:
    return "data:image/png;base64," + base64.b64encode(f"img-{i}".encode()).decode()


def _backend(path_overrides=None):
    spec = ENDPOINT_REGISTRY["ark-seedream-4"]
    if path_overrides is None:
        return spec.build_backend(_PROVIDER, _MODEL)
    return spec.build_backend(_PROVIDER, _MODEL, path_overrides)


async def test_text_to_image_posts_generations_with_the_ark_size(tmp_path: Path):
    with capture_http() as router:
        route = router.post(_GENERATIONS).mock(return_value=_image_response())
        result = await _backend().generate(
            ImageGenerationRequest(prompt="猫", output_path=tmp_path / "o.png", aspect_ratio="9:16")
        )

    request = only_request(route)
    assert request.headers["authorization"] == "Bearer sk"
    body = request_json(request)
    assert body["model"] == _MODEL
    assert body["prompt"] == "猫"
    assert body["size"] == "1600x2848"
    # The Ark SDK serializes absent fields as null.
    assert body.get("image") is None
    assert result.usage is not None
    assert result.usage.quantities["output_images"] == 1
    assert result.usage.quantities["input_images"] == 0


async def test_reference_images_go_to_generations_as_an_image_list(tmp_path: Path):
    with capture_http() as router:
        generations = router.post(_GENERATIONS).mock(return_value=_image_response())
        edits = router.post(url__regex=r".*/images/edits$").mock(return_value=_image_response())
        result = await _backend().generate(
            ImageGenerationRequest(
                prompt="猫", output_path=tmp_path / "o.png", aspect_ratio="1:1", reference_images=_refs(tmp_path, 3)
            )
        )

    assert not edits.called
    body = request_json(only_request(generations))
    assert body["image"] == [_data_uri(0), _data_uri(1), _data_uri(2)]
    assert body["size"] == "2048x2048"
    assert result.usage is not None
    assert result.usage.quantities["input_images"] == 3


async def test_the_endpoint_sends_every_reference_and_takes_the_limit_from_the_matched_entry(tmp_path: Path):
    """Seedream generation limits are model facts: the channel does not truncate by model name; effective limits come from matching entries (14 for 4.5)."""
    backend = create_custom_backend(
        provider=_PROVIDER, model_id="doubao-seedream-4-5-251128", endpoint="ark-seedream-4"
    )
    assert isinstance(backend, CustomImageBackend)
    assert await backend.reference_image_limit() is None
    assert await backend_reference_image_limit(backend, provider_id="custom-1") == InputLimit.at_most(14)

    with capture_http() as router:
        route = router.post(_GENERATIONS).mock(return_value=_image_response())
        await backend.generate(
            ImageGenerationRequest(prompt="猫", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 16))
        )

    assert request_json(only_request(route))["image"] == [_data_uri(i) for i in range(16)]


async def test_generate_path_override_is_relative_to_the_raw_base_url(tmp_path: Path):
    with capture_http() as router:
        route = router.post(f"https://relay.example/proxy/seedream/{_MODEL}").mock(return_value=_image_response())
        await _backend({"generate": "/seedream/{{ model }}"}).generate(
            ImageGenerationRequest(prompt="猫", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 2))
        )

    assert request_json(only_request(route))["image"] == [_data_uri(0), _data_uri(1)]


@pytest.mark.parametrize(
    ("base_url", "generations_url"),
    [
        # Host only: append the Ark protocol mount path /api/v3.
        ("https://ark.cn-beijing.volces.com", "https://ark.cn-beijing.volces.com/api/v3/images/generations"),
        # Already ends in /api/v3: do not append it again.
        ("https://relay.example.com/api/v3/", "https://relay.example.com/api/v3/images/generations"),
    ],
)
async def test_factory_builds_a_seedream_backend_on_the_ark_api_root(tmp_path: Path, base_url, generations_url):
    backend = create_custom_backend(provider=_provider(base_url), model_id=_MODEL, endpoint="ark-seedream-4")
    assert isinstance(backend, CustomImageBackend)
    assert backend.name == "custom-7"
    assert backend.model == _MODEL

    with capture_http() as router:
        route = router.post(generations_url).mock(return_value=_image_response())
        await backend.generate(ImageGenerationRequest(prompt="猫", output_path=tmp_path / "o.png"))

    assert request_json(only_request(route))["model"] == _MODEL


@pytest.mark.parametrize("reference_count", [0, 2, 20, None])
def test_estimate_matches_the_builtin_ark_backend(reference_count):
    spec = ENDPOINT_REGISTRY["ark-seedream-4"]
    assert spec.metering_backend is ArkImageBackend
    facts = ImageRequestFacts(model=_MODEL, aspect_ratio="16:9", reference_count=reference_count)

    estimate = custom_image_estimate(spec.metering_backend, facts)

    builtin = ArkImageBackend.estimate_usage(facts)
    assert estimate.dimensions == builtin.dimensions == {"resolution": "2848x1600"}
    for meter, value in builtin.quantities.items():
        assert estimate.quantities[meter] == value


async def test_seedream_saved_on_openai_images_explains_the_404_from_images_edits(tmp_path: Path):
    """Seedream on an OpenAI image endpoint: a 404 with references suggests switching endpoints."""
    backend = ENDPOINT_REGISTRY["openai-images"].build_backend(_PROVIDER, _MODEL)
    with capture_http() as router:
        router.post("https://relay.example/proxy/v1/images/edits").mock(
            return_value=httpx.Response(404, json={"error": {"code": "NotFound", "message": "not found"}})
        )
        with pytest.raises(ImageCapabilityError) as excinfo:
            await backend.generate(
                ImageGenerationRequest(prompt="猫", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 1))
            )

    assert excinfo.value.code == "image_seedream_edits_unavailable"
    assert excinfo.value.params == {"model": _MODEL}
    stored = encode_task_failure_message(excinfo.value)
    assert parse_failure(stored) == ("image_seedream_edits_unavailable", {"model": _MODEL})
    messages = [
        render_failure(stored, partial(translate_message, locale=locale), make_display_names(locale))
        for locale in ("zh", "en", "vi")
    ]
    assert len(set(messages)) == 3
    for message in messages:
        assert message is not None
        assert "Seedream (1K)" in message
        assert "Seedream (2K)" in message
        assert "image_seedream_edits_unavailable" not in message
        assert "404" not in message


async def test_other_models_keep_the_raw_404_from_images_edits(tmp_path: Path):
    backend = ENDPOINT_REGISTRY["openai-images"].build_backend(_PROVIDER, "gpt-image-2")
    with capture_http() as router:
        router.post("https://relay.example/proxy/v1/images/edits").mock(
            return_value=httpx.Response(404, json={"error": {"code": "model_not_found", "message": "no model"}})
        )
        with pytest.raises(openai.NotFoundError):
            await backend.generate(
                ImageGenerationRequest(prompt="猫", output_path=tmp_path / "o.png", reference_images=_refs(tmp_path, 1))
            )
