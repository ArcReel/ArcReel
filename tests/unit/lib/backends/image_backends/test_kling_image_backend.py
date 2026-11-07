"""KlingImageBackend transport-level respx tests, without real HTTP.

Cover JWT/Bearer auth, text/image generation payloads, complete reference transmission/single-image
overflow rejection, missing-reference fail-loud behavior, sanitized logs, submit/poll/image_url/
download end-to-end, terminal failures, and first-image selection from multiple outputs.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

import httpx
import jwt
import pytest
import respx

from lib.backends.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.image_backends.kling import KlingImageBackend
from lib.backends.providers import PROVIDER_KLING
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request

_SECRET = "s" * 40
_GENERATIONS_URL = "https://api-beijing.klingai.com/v1/images/generations"


class _KlingImageRoutes(NamedTuple):
    """Kling image outbound traffic: creation, polling, and output downloads."""

    submit: respx.Route
    poll: respx.Route
    download: respx.Route


@contextmanager
def _kling_image_api() -> Generator[_KlingImageRoutes]:
    with capture_http() as router:
        yield _KlingImageRoutes(
            submit=router.post(_GENERATIONS_URL),
            poll=router.get(url__regex=rf"^{re.escape(_GENERATIONS_URL)}/[^/]+$"),
            download=router.get(url__regex=r"^https://x/"),
        )


def _resp(json_body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=json_body)


def _submit(task_id: str = "t-1") -> dict:
    return {"code": 0, "message": "SUCCEED", "data": {"task_id": task_id, "task_status": "submitted"}}


def _query(status: str, urls: list[str] | None = None, status_msg: str = "") -> dict:
    data: dict = {"task_id": "t-1", "task_status": status, "task_status_msg": status_msg}
    if urls:
        data["task_result"] = {"images": [{"index": i, "url": u} for i, u in enumerate(urls)]}
    return {"code": 0, "message": "SUCCEED", "data": data}


def _jwt_backend(model: str | None = None, api_model_name: str | None = None) -> KlingImageBackend:
    return KlingImageBackend(
        auth_mode="jwt", access_key="ak-1", secret_key=_SECRET, model=model, api_model_name=api_model_name
    )


def _bearer_backend(model: str | None = None) -> KlingImageBackend:
    return KlingImageBackend(auth_mode="bearer", api_key="static-key", model=model)


def _request(tmp_path: Path, **overrides) -> ImageGenerationRequest:
    kwargs: dict = {
        "prompt": "a hero portrait",
        "output_path": tmp_path / "out.png",
        "aspect_ratio": "9:16",
    }
    kwargs.update(overrides)
    return ImageGenerationRequest(**kwargs)


def _ref(tmp_path: Path, name: str) -> ReferenceImage:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n" + name.encode())
    return ReferenceImage(path=str(p))


class TestConstructionAndCapabilities:
    def test_name_and_default_model(self):
        b = _jwt_backend()
        assert b.name == PROVIDER_KLING
        assert b.model == "kling-image-o1"

    def test_explicit_model_keeps_registry_key(self):
        # model is the registry key for result.model/billing lookup, even with API-name aliases.
        b = _jwt_backend("kling-v3-omni-image", api_model_name="kling-v3-omni")
        assert b.model == "kling-v3-omni-image"

    def test_jwt_missing_credentials_raises(self):
        with pytest.raises(ValueError, match=r"请到系统配置页填写可灵 Kling 的 Access Key 与 Secret Key"):
            KlingImageBackend(auth_mode="jwt", access_key="ak", secret_key=None)

    def test_bearer_missing_api_key_raises(self):
        with pytest.raises(ValueError, match=r"请填写可灵 Kling 的 API Key"):
            KlingImageBackend(auth_mode="bearer", api_key=None)

    def test_unknown_auth_mode_raises(self):
        with pytest.raises(ValueError, match=r"未知 Kling auth_mode"):
            KlingImageBackend(auth_mode="oauth", api_key="k")

    def test_capabilities_t2i_and_i2i(self):
        caps = _jwt_backend().capabilities
        assert ImageCapability.TEXT_TO_IMAGE in caps
        assert ImageCapability.IMAGE_TO_IMAGE in caps


class TestAuthHeaders:
    def test_jwt_mode_signs_bearer_token(self):
        headers = _jwt_backend()._headers()
        assert headers["Content-Type"] == "application/json"
        token = headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, _SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert claims["iss"] == "ak-1"

    def test_bearer_mode_uses_static_key(self):
        headers = _bearer_backend()._headers()
        assert headers["Authorization"] == "Bearer static-key"


class TestApiModelNameResolution:
    def test_alias_key_sends_api_model_name(self, tmp_path):
        # Registry/API alias keys send actual API model_name.
        b = _jwt_backend("kling-v3-omni-image", api_model_name="kling-v3-omni")
        payload = b._build_payload(_request(tmp_path))
        assert payload["model_name"] == "kling-v3-omni"

    def test_plain_key_sends_itself(self, tmp_path):
        # Ordinary nonalias model_name falls back to registry keys.
        b = _jwt_backend("kling-image-o1")
        payload = b._build_payload(_request(tmp_path))
        assert payload["model_name"] == "kling-image-o1"

    def test_default_model_sends_itself(self, tmp_path):
        payload = _jwt_backend()._build_payload(_request(tmp_path))
        assert payload["model_name"] == "kling-image-o1"


class TestPayloadBuilding:
    def test_text2image_no_reference(self, tmp_path):
        payload = _jwt_backend()._build_payload(_request(tmp_path))
        assert payload["model_name"] == "kling-image-o1"
        assert payload["aspect_ratio"] == "9:16"
        assert payload["n"] == 1
        assert "image" not in payload

    def test_image2image_embeds_base64_array(self, tmp_path):
        refs = [_ref(tmp_path, "a.png"), _ref(tmp_path, "b.png")]
        payload = _jwt_backend()._build_payload(_request(tmp_path, reference_images=refs))
        assert isinstance(payload["image"], list)
        assert len(payload["image"]) == 2
        # Raw base64 without data-URI prefixes.
        assert all(isinstance(u, str) and u and not u.startswith("data:") for u in payload["image"])

    def test_channel_sends_every_reference_without_a_model_name_limit(self, tmp_path):
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(12)]
        backend = _jwt_backend()
        payload = backend._build_payload(_request(tmp_path, reference_images=refs))
        # Model reference caps live in entries; orchestration crops by effective limits, never channel model-name truncation.
        assert len(payload["image"]) == 12
        assert asyncio.run(backend.reference_image_limit()) is None

    def test_missing_reference_raises(self, tmp_path):
        bad = ReferenceImage(path=str(tmp_path / "nope.png"))
        with pytest.raises(ImageCapabilityError) as exc:
            _jwt_backend()._build_payload(_request(tmp_path, reference_images=[bad]))
        assert exc.value.code == "image_reference_images_unreadable"

    def test_empty_filename_path_uses_index_placeholder(self, tmp_path):
        # Dot paths resolve empty nonfile names; errors use #N rather than omit tokens.
        bad = ReferenceImage(path=".")
        with pytest.raises(ImageCapabilityError) as exc:
            _jwt_backend()._build_payload(_request(tmp_path, reference_images=[bad]))
        assert exc.value.params["names"] == "#1"


class TestSafeLogView:
    def test_no_base64_or_prompt_leaks(self, tmp_path):
        refs = [_ref(tmp_path, "a.png")]
        b = _jwt_backend()
        payload = b._build_payload(_request(tmp_path, reference_images=refs))
        view = b._safe_log_view(payload)
        assert view["reference_count"] == 1
        assert view["prompt_len"] == len("a hero portrait")
        assert "image" not in view
        assert "prompt" not in view
        assert all(isinstance(v, (str, int, bool)) for v in view.values())


class TestGenerateHappyPath:
    async def test_submit_poll_download(self, tmp_path):
        with _kling_image_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_resp(_submit("task-9")))
            routes.poll.mock(
                side_effect=[
                    _resp(_query("processing")),
                    _resp(_query("succeed", urls=["https://x/final.png"])),
                ]
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"png-bytes"))

            result = await _jwt_backend().generate(_request(tmp_path))

            # images/generations submission endpoint
            assert only_request(routes.submit).url.path == "/v1/images/generations"
            assert routes.poll.calls.last.request.url.path == "/v1/images/generations/task-9"

        assert result.provider == PROVIDER_KLING
        assert result.image_uri == "https://x/final.png"
        assert result.image_path.read_bytes() == b"png-bytes"
        # Payloads omit resolution, so reports omit it too.
        assert result.usage is not None
        assert result.usage.dimensions == {}
        assert result.usage.quantities == {"output_images": 1, "input_images": 0}

    async def test_multiple_images_takes_first(self, tmp_path):
        with _kling_image_api() as routes:
            routes.submit.mock(return_value=_resp(_submit()))
            routes.poll.mock(return_value=_resp(_query("succeed", urls=["https://x/1.png", "https://x/2.png"])))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            result = await _jwt_backend().generate(_request(tmp_path))

            assert str(only_request(routes.download).url) == "https://x/1.png"

        assert result.image_uri == "https://x/1.png"

    async def test_jwt_injected_on_submit(self, tmp_path):
        with _kling_image_api() as routes:
            routes.submit.mock(return_value=_resp(_submit()))
            routes.poll.mock(return_value=_resp(_query("succeed", urls=["https://x/v.png"])))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            await _jwt_backend().generate(_request(tmp_path))

            token = only_request(routes.submit).headers["Authorization"].removeprefix("Bearer ")

        claims = jwt.decode(token, _SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert claims["iss"] == "ak-1"

    async def test_bearer_static_key_on_submit(self, tmp_path):
        with _kling_image_api() as routes:
            routes.submit.mock(return_value=_resp(_submit()))
            routes.poll.mock(return_value=_resp(_query("succeed", urls=["https://x/v.png"])))
            routes.download.mock(return_value=httpx.Response(200, content=b""))

            await _bearer_backend().generate(_request(tmp_path))

            assert only_request(routes.submit).headers["Authorization"] == "Bearer static-key"

    async def test_failed_status_raises(self, tmp_path):
        with _kling_image_api() as routes:
            routes.submit.mock(return_value=_resp(_submit()))
            routes.poll.mock(return_value=_resp(_query("failed", status_msg="content rejected")))

            with pytest.raises(RuntimeError, match="content rejected"):
                await _jwt_backend().generate(_request(tmp_path))

            assert routes.download.call_count == 0

    async def test_http_error_raises_and_no_retry_on_4xx(self, tmp_path):
        # Submission raise_for_status preserves HTTPStatusError; deterministic 4xx never retries
        # nonidempotent creation POST, preventing duplicate tasks/billing.
        with _kling_image_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=httpx.Response(400, text="Bad Request"))

            with pytest.raises(httpx.HTTPStatusError):
                await _jwt_backend().generate(_request(tmp_path))

            assert routes.submit.call_count == 1


class TestRegistration:
    def test_kling_image_backend_registered(self):
        # Trigger image_backends package auto-registration.
        from lib.backends.image_backends import create_backend, get_registered_backends

        assert PROVIDER_KLING in get_registered_backends()
        backend = create_backend(PROVIDER_KLING, auth_mode="jwt", access_key="ak", secret_key=_SECRET)
        assert isinstance(backend, KlingImageBackend)


@pytest.mark.parametrize("endpoint", ["kling-image", "kling-image-single"])
@pytest.mark.parametrize("auth", ["jwt", "bearer", "custom"])
@pytest.mark.parametrize("with_reference", [False, True])
async def test_endpoint_request_shape_and_input_metering(tmp_path, endpoint, auth, with_reference):
    from lib.backends.backend_assembly.loaded_config import LoadedConfig
    from lib.backends.backend_assembly.specs import get_provider_spec
    from lib.config.registry import PROVIDER_REGISTRY
    from lib.custom_provider.endpoints import endpoint_reference_image_limit, get_endpoint_spec

    if auth == "custom":
        provider = SimpleNamespace(
            provider_id="custom-test", api_key="static-key", base_url="https://api-beijing.klingai.com"
        )
        backend = get_endpoint_spec(endpoint).build_backend(provider, "kling-v3")
    else:
        credentials = {"access_key": "ak-1", "secret_key": _SECRET} if auth == "jwt" else {"api_key": "static-key"}
        backend = get_provider_spec("kling", "image", endpoint).build_backend(
            LoadedConfig(credentials=credentials, provider_meta=PROVIDER_REGISTRY["kling"], rate_limiter=None),
            "kling-v3",
        )
    refs = [_ref(tmp_path, "a.png")] if with_reference else []
    with _kling_image_api() as routes:
        routes.submit.mock(return_value=_resp(_submit()))
        routes.poll.mock(return_value=_resp(_query("succeed", urls=["https://x/final.png"])))
        routes.download.mock(return_value=httpx.Response(200, content=b"png-bytes"))
        result = await backend.generate(_request(tmp_path, reference_images=refs))
        payload = json.loads(only_request(routes.submit).content)

    assert payload["model_name"] == "kling-v3"
    if with_reference:
        encoded = base64.b64encode((tmp_path / "a.png").read_bytes()).decode()
        assert payload["image"] == (encoded if endpoint == "kling-image-single" else [encoded])
    else:
        assert "image" not in payload
    assert result.image_path.read_bytes() == b"png-bytes"
    assert result.usage is not None
    assert result.usage.quantities == {"output_images": 1, "input_images": int(with_reference)}
    expected_limit = 1 if endpoint == "kling-image-single" else None
    assert await backend.reference_image_limit() == expected_limit
    assert endpoint_reference_image_limit(get_endpoint_spec(endpoint)) == expected_limit


async def test_single_image_rejects_multiple_references_before_submit(tmp_path):
    from lib.backends.image_backends.kling import KlingSingleImageBackend

    backend = KlingSingleImageBackend(auth_mode="bearer", api_key="test-bearer")
    refs = [_ref(tmp_path, "a.png"), _ref(tmp_path, "b.png")]
    from lib.backends.image_capabilities import gate_image_request
    from lib.custom_provider.backends import CustomImageBackend

    backend = CustomImageBackend(
        provider_id="custom-1",
        delegate=backend,
        model=backend.model,
        reference_limit_override=2,
    )
    await gate_image_request(backend, provider_id="custom-1", reference_count=2)
    with capture_http() as router, pytest.raises(ImageCapabilityError) as exc:
        await backend.generate(_request(tmp_path, reference_images=refs))

    assert exc.value.code == "image_reference_images_exceeded"
    assert exc.value.params["limit"] == 1
    assert exc.value.params["count"] == 2
    assert not router.calls
