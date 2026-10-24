"""ViduImageBackend 单元测试 — 重点校验 prompt/aspect_ratio 白名单兜底与 resolution 原样透传。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from lib.backends.http_status_errors import ProviderRejectedError
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.image_backends.vidu import (
    _ASPECT_RATIO_WHITELIST,
    _MAX_REFERENCE_IMAGES,
    _PROMPT_MAX_LEN,
    DEFAULT_MODEL,
    ViduImageBackend,
)
from lib.backends.providers import PROVIDER_VIDU
from tests.http_capture import capture_http


@pytest.fixture
def image_output_path(tmp_path: Path) -> Path:
    return tmp_path / "out.png"


class TestViduImageBackendBasics:
    def test_name_and_default_model(self):
        backend = ViduImageBackend(api_key="test-key")
        assert backend.name == PROVIDER_VIDU
        assert backend.model == DEFAULT_MODEL == "viduq2"

    def test_custom_model(self):
        backend = ViduImageBackend(api_key="test-key", model="viduq1")
        assert backend.model == "viduq1"

    def test_q2_capabilities_include_t2i_and_i2i(self):
        backend = ViduImageBackend(api_key="test-key", model="viduq2")
        assert ImageCapability.TEXT_TO_IMAGE in backend.capabilities
        assert ImageCapability.IMAGE_TO_IMAGE in backend.capabilities


class TestWhitelistConfig:
    """避免运行时调用，先把配置常量当 spec 钉死，防止误改。"""

    def test_q1_aspect_ratio_no_2_3_or_3_2(self):
        # 文档：viduq1 只允许 16:9 9:16 1:1 3:4 4:3
        assert "2:3" not in _ASPECT_RATIO_WHITELIST["viduq1"]
        assert "3:2" not in _ASPECT_RATIO_WHITELIST["viduq1"]

    def test_q2_aspect_ratio_includes_auto(self):
        assert "auto" in _ASPECT_RATIO_WHITELIST["viduq2"]

    def test_reference_image_limit_is_seven(self):
        assert _MAX_REFERENCE_IMAGES == 7
        assert asyncio.run(ViduImageBackend(api_key="k", model="viduq2").reference_image_limit()) == 7

    def test_prompt_truncation_limit(self):
        assert _PROMPT_MAX_LEN == 2000


class TestEstimateUsage:
    """预估的分辨率维度与请求同源：请求带什么档位就报什么。"""

    def test_requested_resolution_is_reported(self):
        from lib.backends.image_backends.base import ImageRequestFacts

        usage = ViduImageBackend.estimate_usage(
            ImageRequestFacts(model="viduq2", aspect_ratio="9:16", image_size="2K", reference_count=3)
        )
        assert usage.dimensions == {"resolution": "2K"}
        assert usage.quantities == {"output_images": 1, "input_images": 3}

    def test_no_resolution_when_not_requested(self):
        from lib.backends.image_backends.base import ImageRequestFacts

        usage = ViduImageBackend.estimate_usage(ImageRequestFacts(model="viduq1", aspect_ratio="9:16"))
        assert usage.dimensions == {}


class TestCapabilityMismatchRaises:
    async def test_q2_with_refs_path_does_not_raise_capability(
        self, tmp_path: Path, image_output_path: Path, monkeypatch
    ):
        # 仅校验 capability 检查不会先拦下来；实际生成走 mock。
        # 让 create_vidu_client 抛错是为了快速短路，避免真正发请求。
        from lib.backends.image_backends import vidu as mod

        def _boom(*a, **kw):
            raise RuntimeError("short-circuit")

        monkeypatch.setattr(mod, "create_vidu_client", _boom)
        ref_file = tmp_path / "ref.png"
        ref_file.write_bytes(b"\x89PNG\r\n\x1a\n")
        backend = ViduImageBackend(api_key="test-key", model="viduq2")
        request = ImageGenerationRequest(
            prompt="hello",
            output_path=image_output_path,
            reference_images=[ReferenceImage(path=str(ref_file))],
        )
        # 不应是 ImageCapabilityError；应是我们注入的 RuntimeError
        with pytest.raises(RuntimeError, match="short-circuit"):
            await backend.generate(request)


class TestViduReferenceImageLimit:
    async def test_declared_limit_equals_what_it_sends(self, tmp_path: Path, image_output_path: Path):
        """声明的上限即超量时实际下传的张数——编排层按它裁剪，编号才不会指认没发出的图。

        建任务请求以 400 短路（后端把它折成 ProviderRejectedError），只看已发出的请求体。
        """
        refs = []
        for index in range(9):
            ref_path = tmp_path / f"r{index}.png"
            ref_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            refs.append(ReferenceImage(path=str(ref_path)))

        with capture_http() as router:
            route = router.post("https://api.vidu.com/ent/v2/reference2image").mock(
                return_value=httpx.Response(400, json={"code": 1013, "message": "nope"})
            )
            backend = ViduImageBackend(api_key="k", model="viduq2", base_url="https://api.vidu.com/ent/v2")
            with pytest.raises(ProviderRejectedError):
                await backend.generate(
                    ImageGenerationRequest(prompt="x", output_path=image_output_path, reference_images=refs)
                )

        import json

        body = json.loads(route.calls.last.request.content)
        assert len(body["images"]) == (await backend.reference_image_limit()) == 7


class TestViduImageCreateTaskHttpErrors:
    """_create_task 的 >=400 收口：保留状态码（咽喉层据此降档），4xx 另带脱敏拒因摘要。"""

    async def test_create_task_413_surfaces_httpstatuserror_no_retry(self):
        from unittest.mock import AsyncMock, MagicMock

        import httpx

        request = httpx.Request("POST", "https://vidu/reference2image")
        response = httpx.Response(413, request=request, text="Request Entity Too Large")
        err = httpx.HTTPStatusError("error 413", request=request, response=response)

        resp = MagicMock()
        resp.status_code = 413
        resp.text = "Request Entity Too Large"
        resp.raise_for_status = MagicMock(side_effect=err)
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        backend = ViduImageBackend(api_key="k", model="viduq2")
        with pytest.raises(httpx.HTTPStatusError) as ei:
            await backend._create_task(client, {"model": "viduq2", "prompt": "x"})
        assert ei.value.response.status_code == 413
        # 413 非 retryable → fail-fast 单次
        assert client.post.call_count == 1

    async def test_rejection_body_becomes_a_redacted_provider_reason(self, image_output_path: Path):
        """4xx 与视频侧同路：消息去掉查询串，拒因摘要单独挂在异常上。"""
        with capture_http() as router:
            router.post("https://api.vidu.com/ent/v2/reference2image").mock(
                return_value=httpx.Response(400, json={"code": 1013, "message": "reference image is not readable"})
            )
            backend = ViduImageBackend(api_key="k", model="viduq2", base_url="https://api.vidu.com/ent/v2")
            with pytest.raises(ProviderRejectedError) as ei:
                await backend.generate(ImageGenerationRequest(prompt="x", output_path=image_output_path))

        assert ei.value.response.status_code == 400
        assert ei.value.provider_reason == "1013: reference image is not readable"


class TestViduImageResolutionPassthrough:
    @pytest.mark.parametrize(("model", "resolution"), [("viduq1", "2K"), ("viduq2", "8K")])
    async def test_resolution_is_sent_as_requested_and_upstream_rejection_is_readable(
        self, image_output_path: Path, model: str, resolution: str
    ):
        """档位以模型目录条目为准：越界值照发，由上游拒绝并以拒因摘要返回（docs/adr/0104）。"""
        import json

        with capture_http() as router:
            route = router.post("https://api.vidu.com/ent/v2/reference2image").mock(
                return_value=httpx.Response(400, json={"code": 1013, "message": "resolution not supported"})
            )
            backend = ViduImageBackend(api_key="k", model=model, base_url="https://api.vidu.com/ent/v2")
            with pytest.raises(ProviderRejectedError) as ei:
                await backend.generate(
                    ImageGenerationRequest(prompt="x", output_path=image_output_path, image_size=resolution)
                )

        assert json.loads(route.calls.last.request.content)["resolution"] == resolution
        assert ei.value.provider_reason == "1013: resolution not supported"
