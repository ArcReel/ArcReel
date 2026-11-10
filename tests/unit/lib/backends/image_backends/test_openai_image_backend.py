"""OpenAIImageBackend 单元测试。"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arcreel_catalog_core import ScaledOutputTokenBase
from lib.backends.image_backends import ImageCapabilityError
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.providers import PROVIDER_OPENAI
from tests.fakes import captured_openai_clients
from tests.http_capture import capture_http, only_request, request_json


def _make_mock_image_response(
    b64_data: str | None = "aW1hZ2VfZGF0YQ==",
    url: str | None = None,
    usage=None,
):
    """构造 mock ImagesResponse。

    - usage=None 时显式不挂 usage 属性（模拟 SDK 不返回 usage 的情况）
    - 传 dict {"input_tokens": ..., "output_tokens": ..., "input_tokens_details": {...}, "output_tokens_details": {...}}
      则按 dict 键构造嵌套 MagicMock
    """
    datum = MagicMock()
    datum.b64_json = b64_data
    datum.url = url

    # spec 限定属性集，避免 MagicMock 自动创造未知属性
    response = MagicMock(spec=["data", "usage"])
    response.data = [datum]
    if usage is None:
        response.usage = None
    else:
        usage_obj = MagicMock()
        usage_obj.input_tokens = usage.get("input_tokens")
        usage_obj.output_tokens = usage.get("output_tokens")
        in_d = usage.get("input_tokens_details")
        if in_d is None:
            usage_obj.input_tokens_details = None
        else:
            details = MagicMock()
            details.image_tokens = in_d.get("image_tokens")
            details.text_tokens = in_d.get("text_tokens")
            usage_obj.input_tokens_details = details
        out_d = usage.get("output_tokens_details")
        if out_d is None:
            usage_obj.output_tokens_details = None
        else:
            details = MagicMock()
            details.image_tokens = out_d.get("image_tokens")
            details.text_tokens = out_d.get("text_tokens")
            usage_obj.output_tokens_details = details
        response.usage = usage_obj
    return response


class TestOpenAIImageBackend:
    def test_name_and_model(self):
        with captured_openai_clients():
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "gpt-image-2"

    def test_custom_model(self):
        with captured_openai_clients():
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key", model="custom-image-model")
            assert backend.model == "custom-image-model"

    def test_capabilities(self):
        with captured_openai_clients():
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            assert ImageCapability.TEXT_TO_IMAGE in backend.capabilities
            assert ImageCapability.IMAGE_TO_IMAGE in backend.capabilities

    async def test_text_to_image(self, tmp_path: Path):
        """T2I 路径应调用 images.generate()。"""
        b64_data = base64.b64encode(b"fake-png-data").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "output.png"
            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=output_path,
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.provider == PROVIDER_OPENAI
        assert result.model == "gpt-image-2"
        assert result.image_path == output_path
        assert output_path.read_bytes() == b"fake-png-data"

        mock_client.images.generate.assert_awaited_once()
        call_kwargs = mock_client.images.generate.call_args[1]
        assert call_kwargs["model"] == "gpt-image-2"
        assert call_kwargs["size"] == "1008x1792"  # 9:16 精确（1K 短边 1024）
        assert call_kwargs["quality"] == "medium"  # 1K
        # GPT Image 模型族不支持 response_format；严格兼容网关会 400，因此绝不能传
        assert "response_format" not in call_kwargs

    async def test_sends_every_reference_above_the_channel_limit(self, tmp_path: Path):
        """能力覆盖放行的参考图全部下发。"""
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(return_value=_make_mock_image_response())

        refs = []
        for index in range(20):
            ref_path = tmp_path / f"ref{index}.png"
            ref_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            refs.append(ReferenceImage(path=str(ref_path)))

        output_path = tmp_path / "output.png"
        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            from lib.backends.image_capabilities import gate_image_request
            from lib.custom_provider.backends import CustomImageBackend

            backend = CustomImageBackend(
                provider_id="custom-1",
                delegate=backend,
                model=backend.model,
                reference_limit_override=20,
            )
            await gate_image_request(backend, provider_id="custom-1", reference_count=20)
            result = await backend.generate(
                ImageGenerationRequest(
                    prompt="Edit these",
                    output_path=output_path,
                    reference_images=refs,
                )
            )

        assert result.image_path == output_path
        assert output_path.read_bytes() == b"image_data"
        assert len(mock_client.images.edit.call_args[1]["image"]) == 20

    async def test_image_to_image(self, tmp_path: Path):
        """I2I 路径应调用 images.edit()。"""
        b64_data = base64.b64encode(b"edited-image").decode()
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(return_value=_make_mock_image_response(b64_data))

        ref_path = tmp_path / "ref.png"
        ref_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "output.png"
            request = ImageGenerationRequest(
                prompt="Edit this image",
                output_path=output_path,
                reference_images=[ReferenceImage(path=str(ref_path))],
            )
            result = await backend.generate(request)

        assert result.image_path == output_path
        assert output_path.read_bytes() == b"edited-image"
        mock_client.images.edit.assert_awaited_once()
        mock_client.images.generate.assert_not_awaited()
        edit_kwargs = mock_client.images.edit.call_args[1]
        assert "response_format" not in edit_kwargs
        # I2I 与 T2I 对称下传 size：默认 9:16 + image_size=None → 兜底 720 短边 → 720x1280。
        assert edit_kwargs["size"] == "720x1280"

    async def test_empty_data_raises(self, tmp_path: Path):
        """OpenAI 返回空 data 数组时，应抛出清晰的 RuntimeError 而非 IndexError。"""
        empty_response = MagicMock(spec=["data", "usage"])
        empty_response.data = []
        empty_response.usage = None

        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=empty_response)

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=tmp_path / "output.png",
                aspect_ratio="9:16",
                image_size="1K",
            )

            with pytest.raises(RuntimeError, match="data 为空"):
                await backend.generate(request)

    async def test_text_to_image_url_fallback(self, tmp_path: Path):
        """网关只返回 url 时，应走 httpx 下载分支。"""
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(b64_data=None, url="https://gateway/img.png")
        )

        downloaded = b"downloaded-from-gateway"

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            output_path = tmp_path / "output.png"
            request = ImageGenerationRequest(
                prompt="A beautiful sunset",
                output_path=output_path,
                aspect_ratio="9:16",
                image_size="1K",
            )

            with capture_http() as router:
                download = router.get("https://gateway/img.png").mock(
                    return_value=httpx.Response(200, content=downloaded)
                )
                result = await backend.generate(request)

            assert str(only_request(download).url) == "https://gateway/img.png"

        assert result.image_path == output_path
        assert output_path.read_bytes() == downloaded

    async def test_size_mapping(self, tmp_path: Path):
        """验证 aspect_ratio 精确决定 size（1K 短边 1024，比例零偏差）。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            # image_size="1K"（短边 1024）下遍历不同 aspect_ratio，比例精确
            for aspect, expected_size in [("16:9", "1792x1008"), ("1:1", "1024x1024"), ("9:16", "1008x1792")]:
                output_path = tmp_path / f"output_{aspect.replace(':', '_')}.png"
                request = ImageGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    aspect_ratio=aspect,
                    image_size="1K",
                )
                await backend.generate(request)
                call_kwargs = mock_client.images.generate.call_args[1]
                assert call_kwargs["size"] == expected_size, f"aspect={aspect}"

    async def test_quality_mapping(self, tmp_path: Path):
        """验证 image_size → quality 映射（标准 token）。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")

            # quality 仍按 image_size 档位映射；遍历有映射的档位
            for img_size, expected_quality in [("512px", "low"), ("1K", "medium"), ("2K", "high")]:
                output_path = tmp_path / f"output_{img_size}.png"
                request = ImageGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    aspect_ratio="9:16",
                    image_size=img_size,
                )
                await backend.generate(request)
                call_kwargs = mock_client.images.generate.call_args[1]
                assert call_kwargs["quality"] == expected_quality, f"size={img_size}"

    @pytest.mark.parametrize("model", ["gpt-image-2", "gpt-image-2.6-sunburst", "gpt-image-2.6-flare"])
    async def test_text_to_image_captures_usage(self, tmp_path: Path, model: str):
        """SDK 返回 usage 时，结果携带实际 token 拆分字段，不用估算值替代。"""
        b64_data = base64.b64encode(b"img").decode()
        with capture_http() as router:
            route = router.post("https://api.openai.com/v1/images/generations").respond(
                json={
                    "created": 0,
                    "data": [{"b64_json": b64_data}],
                    "usage": {
                        "input_tokens": 500,
                        "output_tokens": 2200,
                        "total_tokens": 2700,
                        "input_tokens_details": {"text_tokens": 500, "image_tokens": 0},
                        # 不返回 output_tokens_details；img_out 应回退到顶层 output_tokens
                    },
                }
            )
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key", model=model)
            request = ImageGenerationRequest(
                prompt="usage capture",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)
            assert request_json(only_request(route)) == {
                "model": model,
                "prompt": "usage capture",
                "n": 1,
                "size": "1008x1792",
                "quality": "medium",
            }

        assert result.text_input_tokens == 500
        assert result.image_input_tokens == 0
        assert result.image_output_tokens == 2200  # 顶层 fallback
        assert result.text_output_tokens is None
        # 用量契约：按 token 上报，选择维度取自请求里的 size 与 quality
        assert result.usage is not None
        assert result.usage.quantities == {
            "uncached_input_tokens": 500,
            "uncached_image_input_tokens": 0,
            "image_output_tokens": 2200,
        }
        assert result.usage.dimensions == {"resolution": "1008x1792", "quality": "medium"}

    async def test_text_to_image_no_usage_returns_none(self, tmp_path: Path):
        """SDK 不返回 usage 时，4 个 token 字段全部为 None。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(return_value=_make_mock_image_response(b64_data))

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="no usage",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.image_input_tokens is None
        assert result.image_output_tokens is None
        assert result.text_input_tokens is None
        assert result.text_output_tokens is None
        # 拿不到 usage：只报输出张数，不按尺寸与质量估 token 记账
        assert result.usage is not None
        assert result.usage.quantities == {"output_images": 1}

    async def test_partial_usage_no_input_details_falls_back(self, tmp_path: Path):
        """usage 顶层 input_tokens 存在但 input_tokens_details 缺失：4 字段全 None，让 cost 走静态 fallback。

        防御兼容网关或字段裁剪响应：缺 input 拆分时若仍走 token 路径会系统性漏算 input 成本。
        """
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    "input_tokens": 500,  # 顶层有
                    "output_tokens": 2200,
                    "input_tokens_details": None,  # 但拆分缺失
                },
            )
        )

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="partial usage",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.image_input_tokens is None
        assert result.image_output_tokens is None
        assert result.text_input_tokens is None
        assert result.text_output_tokens is None

    async def test_input_details_empty_inner_fields_falls_back(self, tmp_path: Path):
        """input_tokens_details 对象在但 image_tokens / text_tokens 都为 None：等同于无拆分，fallback。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    "input_tokens": 500,
                    "output_tokens": 2200,
                    "input_tokens_details": {"image_tokens": None, "text_tokens": None},
                },
            )
        )

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="empty details",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.image_input_tokens is None
        assert result.text_input_tokens is None
        assert result.image_output_tokens is None
        assert result.text_output_tokens is None

    async def test_input_details_present_but_no_output_falls_back(self, tmp_path: Path):
        """input 拆分到手但完全拿不到 output 信息：4 字段全部撤回，避免只算 input 漏算 output。"""
        b64_data = base64.b64encode(b"img").decode()
        mock_client = AsyncMock()
        mock_client.images.generate = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    # 没有 output_tokens 顶层、没有 output_tokens_details
                    "input_tokens": 500,
                    "input_tokens_details": {"text_tokens": 500, "image_tokens": 0},
                },
            )
        )

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="no output info",
                output_path=tmp_path / "out.png",
                aspect_ratio="9:16",
                image_size="1K",
            )
            result = await backend.generate(request)

        assert result.image_input_tokens is None
        assert result.image_output_tokens is None
        assert result.text_input_tokens is None
        assert result.text_output_tokens is None

    async def test_image_to_image_captures_image_input_tokens(self, tmp_path: Path):
        """I2I 路径应能解析 input_tokens_details.image_tokens（参考图 token）。"""
        b64_data = base64.b64encode(b"edited").decode()
        mock_client = AsyncMock()
        mock_client.images.edit = AsyncMock(
            return_value=_make_mock_image_response(
                b64_data,
                usage={
                    "input_tokens": 12000,
                    "output_tokens": 2200,
                    "input_tokens_details": {"text_tokens": 200, "image_tokens": 11800},
                    "output_tokens_details": {"image_tokens": 2200, "text_tokens": 0},
                },
            )
        )

        ref_path = tmp_path / "ref.png"
        ref_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

        with captured_openai_clients(mock_client):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            backend = OpenAIImageBackend(api_key="test-key")
            request = ImageGenerationRequest(
                prompt="edit",
                output_path=tmp_path / "out.png",
                reference_images=[ReferenceImage(path=str(ref_path))],
            )
            result = await backend.generate(request)

        assert result.image_input_tokens == 11800
        assert result.text_input_tokens == 200
        assert result.image_output_tokens == 2200
        assert result.text_output_tokens == 0


class TestModeCapabilities:
    def test_default_mode_is_both(self):
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m")
            assert ImageCapability.TEXT_TO_IMAGE in b.capabilities
            assert ImageCapability.IMAGE_TO_IMAGE in b.capabilities

    def test_generations_only_mode(self):
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m", mode="generations_only")
            assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE}

    def test_edits_only_mode(self):
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m", mode="edits_only")
            assert b.capabilities == {ImageCapability.IMAGE_TO_IMAGE}


class TestModeGating:
    @pytest.mark.asyncio
    async def test_generations_only_with_refs_raises(self, tmp_path):
        ref = tmp_path / "r.png"
        ref.write_bytes(b"\x89PNG")
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m", mode="generations_only")
            req = ImageGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.png",
                reference_images=[ReferenceImage(path=str(ref))],
            )
            with pytest.raises(ImageCapabilityError) as excinfo:
                await b.generate(req)
            assert excinfo.value.code == "image_endpoint_mismatch_no_i2i"
            assert excinfo.value.params == {"model": "m"}

    @pytest.mark.asyncio
    async def test_edits_only_without_refs_raises(self, tmp_path):
        with patch("lib.backends.image_backends.openai.create_openai_client"):
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m", mode="edits_only")
            req = ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png")
            with pytest.raises(ImageCapabilityError) as excinfo:
                await b.generate(req)
            assert excinfo.value.code == "image_endpoint_mismatch_no_t2i"

    @pytest.mark.asyncio
    async def test_all_refs_failed_to_open_raises(self, tmp_path):
        """所有 ref 图都打不开时，应抛 ImageCapabilityError 而非回退到 T2I。"""
        with captured_openai_clients():
            from lib.backends.image_backends.openai import OpenAIImageBackend

            b = OpenAIImageBackend(api_key="x", model="m")  # mode="both" 默认
            req = ImageGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.png",
                reference_images=[ReferenceImage(path="/nonexistent/ref.png")],
            )
            with pytest.raises(ImageCapabilityError) as excinfo:
                await b.generate(req)
            assert excinfo.value.code == "image_endpoint_mismatch_no_i2i"
            assert excinfo.value.params.get("model") == "m"
            assert excinfo.value.params.get("detail") == "all reference images failed to open"


def _image_entry(
    name: str,
    output_token_bases: dict[str, int] | None = None,
    output_token_scaled_bases: dict | None = None,
):
    """测试用图片条目：只带输出 token 估算规则（质量档 → 基数或带系数基数），不依赖出厂快照。"""
    from arcreel_catalog_core import CatalogEntry, ImageFacts

    return CatalogEntry(
        maker="openai",
        name=name,
        media_type="image",
        display_name=name,
        facts=ImageFacts(
            text_to_image=True,
            image_to_image=True,
            output_token_bases=output_token_bases or {},
            output_token_scaled_bases=output_token_scaled_bases or {},
        ),
    )


#: 官方计算器的质量档基数（arcreel-catalog 收录的 GPT Image 2 / GPT Image 2.5）。
_GPT_IMAGE_2_BASES = {"low": 16, "medium": 48, "high": 96}
_GPT_IMAGE_2_5_BASES = {"low": 16, "medium": 24, "high": 48, "xhigh": 64, "max": 96}
#: GPT Image 2.6 的目录形状：系数为 1 的档写普通基数，低、中档写带系数基数。
_GPT_IMAGE_2_6_BASES = {"high": 48, "xhigh": 64, "max": 96}
_GPT_IMAGE_2_6_SCALED_BASES = {
    "low": ScaledOutputTokenBase(base=16, multiplier=4.2),
    "medium": ScaledOutputTokenBase(base=24, multiplier=3.3),
}


class TestOutputTokenEstimate:
    """输出 token 估算按条目上的质量档基数套官方计算器公式；条目没收录请求所用质量档时不估算。"""

    @pytest.mark.parametrize(
        ("size", "quality", "tokens", "official_price"),
        [
            ("1024x1024", "low", 196, 0.006),
            ("1024x1536", "low", 158, 0.005),
            ("1024x1024", "medium", 1756, 0.053),
            ("1536x1024", "medium", 1372, 0.041),
            ("1024x1024", "high", 7024, 0.211),
            ("1024x1536", "high", 5488, 0.165),
        ],
    )
    def test_matches_official_table(self, size: str, quality: str, tokens: int, official_price: float) -> None:
        from lib.backends.image_backends.openai import estimate_output_tokens

        assert estimate_output_tokens(size, quality, _GPT_IMAGE_2_BASES) == tokens
        # 官方表按 $30 / 百万输出 token 折成单图价，保留三位小数
        assert round(tokens * 30 / 1_000_000, 3) == official_price

    def test_quality_missing_from_bases_or_unparsable_size_has_no_estimate(self) -> None:
        from lib.backends.image_backends.openai import estimate_output_tokens

        assert estimate_output_tokens("1024x1024", "auto", _GPT_IMAGE_2_BASES) is None
        assert estimate_output_tokens("1024x1024", "medium", {}) is None
        assert estimate_output_tokens("1K", "medium", _GPT_IMAGE_2_BASES) is None

    def test_estimate_usage_uses_request_size_and_quality(self) -> None:
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        usage = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(
                model="gpt-image-2",
                aspect_ratio="9:16",
                image_size="1K",
                reference_count=0,
                entry=_image_entry("gpt-image-2", _GPT_IMAGE_2_BASES),
            )
        )
        assert usage.dimensions == {"resolution": "1008x1792", "quality": "medium"}
        assert usage.quantities["image_output_tokens"] == 1234
        # 提示词文字 token 拿不到，记 0；文生图没有参考图，图片输入 token 记 0
        assert usage.quantities["uncached_input_tokens"] == 0
        assert usage.quantities["uncached_image_input_tokens"] == 0

    @pytest.mark.parametrize(
        ("image_size", "aspect_ratio", "tokens"),
        [
            # 1K → medium：2.5 的 medium 基数是 24（gpt-image-2 是 48），同尺寸 token 约为四分之一
            ("1K", "1:1", 439),
            # 2K → high：2.5 的 high 基数是 48，1440x1440
            ("2K", "1:1", 2_347),
        ],
    )
    def test_gpt_image_2_5_is_estimated_from_its_entry(self, image_size: str, aspect_ratio: str, tokens: int) -> None:
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        usage = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(
                model="gpt-image-2.5",
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                entry=_image_entry("gpt-image-2.5", _GPT_IMAGE_2_5_BASES),
            )
        )
        assert usage.quantities["image_output_tokens"] == tokens

    def test_model_name_does_not_decide_whether_tokens_are_estimated(self) -> None:
        """估算只看条目：不透明的 API 模型名绑到收录了基数的条目也能估；gpt-image-2 的名字配上没收录基数的条目不估。"""
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        def output(model: str, entry) -> object:
            facts = ImageRequestFacts(model=model, aspect_ratio="1:1", image_size="1K", entry=entry)
            return OpenAIImageBackend.estimate_usage(facts).quantities.get("image_output_tokens")

        assert output("relay-image-xyz", _image_entry("gpt-image-2", _GPT_IMAGE_2_BASES)) == 1756
        assert output("gpt-image-2", _image_entry("gpt-image-2")) is None
        assert output("gpt-image-2", None) is None
        # 条目只收录了部分质量档：请求用的 medium 不在里面，不估算
        assert output("gpt-image-2", _image_entry("gpt-image-2", {"low": 16})) is None

    def test_estimate_without_quality_or_with_references_leaves_tokens_unknown(self) -> None:
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        entry = _image_entry("gpt-image-2", _GPT_IMAGE_2_BASES)
        no_quality = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(model="gpt-image-2", aspect_ratio="9:16", image_size=None, entry=entry)
        )
        assert "image_output_tokens" not in no_quality.quantities
        with_refs = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(
                model="gpt-image-2", aspect_ratio="9:16", image_size="1K", reference_count=None, entry=entry
            )
        )
        assert "uncached_image_input_tokens" not in with_refs.quantities


class TestScaledOutputTokenEstimate:
    """带系数的质量档：先按基数走官方公式并向上取整，再乘系数，四舍五入（恰好 .5 进位）。

    期望值由官方计算器的公式算出，不由实现反推：
    https://developers.openai.com/api/docs/guides/image-generation#calculating-costs
    ``p=Math.ceil(f*(2e6+t*n)/4e6);return e==="gpt-image-2.6"?Math.round(p*l[r]):p``，
    其中 ``l={low:4.2,medium:3.3,high:1,xhigh:1,max:1}``。
    """

    @pytest.mark.parametrize(
        ("size", "quality", "tokens"),
        [
            pytest.param("1024x1024", "low", 823, id="方图-low"),
            pytest.param("1024x1024", "medium", 1449, id="方图-medium"),
            pytest.param("1536x1024", "low", 664, id="横图-low"),
            pytest.param("1792x1008", "medium", 1056, id="横图-medium"),
            pytest.param("1024x1536", "low", 664, id="竖图-low"),
            pytest.param("1008x1792", "medium", 1056, id="竖图-medium"),
            pytest.param("1024x1024", "high", 1756, id="系数为1的high"),
            pytest.param("1024x1024", "xhigh", 3122, id="系数为1的xhigh"),
            pytest.param("1024x1024", "max", 7024, id="系数为1的max"),
            # 短边块数恰好 .5 时取偶：16 ÷ (1024÷672) = 10.5 → 10，token 108，108 × 4.2 = 453.6
            pytest.param("672x1024", "low", 454, id="公式内部取偶-low"),
            pytest.param("560x1280", "medium", 541, id="公式内部取偶-medium"),
            # 乘系数后恰好 .5 时进位：165 × 3.3 = 544.5 → 545，145 × 3.3 = 478.5 → 479；round() 取偶会得到 544、478
            pytest.param("544x1360", "medium", 545, id="结果恰好点五进位"),
            pytest.param("496x1360", "medium", 479, id="结果恰好点五进位-奇数"),
            # 先向上取整再乘系数：公式值 63.85 → 64，64 × 4.2 = 268.8 → 269；先乘系数会得到 268
            pytest.param("480x1376", "low", 269, id="先取整后乘系数"),
        ],
    )
    def test_matches_official_calculator(self, size: str, quality: str, tokens: int) -> None:
        from lib.backends.image_backends.openai import estimate_output_tokens

        assert estimate_output_tokens(size, quality, _GPT_IMAGE_2_6_BASES, _GPT_IMAGE_2_6_SCALED_BASES) == tokens

    @pytest.mark.parametrize("folded_base", [67, 33])
    def test_multiplier_cannot_be_folded_into_an_integer_base(self, folded_base: int) -> None:
        """把系数折进整数基数（16 × 4.2 ≈ 67，或 16 × √4.2 ≈ 33）得不到官方的 823，基数在公式里会被平方。"""
        from lib.backends.image_backends.openai import estimate_output_tokens

        assert estimate_output_tokens("1024x1024", "low", {"low": folded_base}) != 823

    def test_unknown_stays_unknown(self) -> None:
        from lib.backends.image_backends.openai import estimate_output_tokens

        assert estimate_output_tokens("1024x1024", "auto", _GPT_IMAGE_2_6_BASES, _GPT_IMAGE_2_6_SCALED_BASES) is None
        assert estimate_output_tokens("1K", "low", _GPT_IMAGE_2_6_BASES, _GPT_IMAGE_2_6_SCALED_BASES) is None
        assert estimate_output_tokens("0x1024", "low", _GPT_IMAGE_2_6_BASES, _GPT_IMAGE_2_6_SCALED_BASES) is None

    def test_old_consumer_view_of_the_published_entry_keeps_low_and_medium_unknown(self) -> None:
        """旧实例忽略带系数基数，只看到系数为 1 的档：低、中档保持未知，不会算出漏乘系数的低估值。"""
        from lib.backends.image_backends.openai import estimate_output_tokens

        assert estimate_output_tokens("1024x1024", "low", _GPT_IMAGE_2_6_BASES) is None
        assert estimate_output_tokens("1024x1024", "medium", _GPT_IMAGE_2_6_BASES) is None
        assert estimate_output_tokens("1024x1024", "high", _GPT_IMAGE_2_6_BASES) == 1756

    @pytest.mark.parametrize(
        ("image_size", "aspect_ratio", "size", "quality", "tokens"),
        [
            pytest.param("512px", "1:1", "512x512", "low", 609, id="512px方图"),
            pytest.param("512px", "16:9", "1024x576", "low", 395, id="512px横图"),
            pytest.param("512px", "9:16", "576x1024", "low", 395, id="512px竖图"),
            pytest.param("1K", "1:1", "1024x1024", "medium", 1449, id="1K方图"),
            pytest.param("1K", "16:9", "1792x1008", "medium", 1056, id="1K横图"),
            pytest.param("1K", "9:16", "1008x1792", "medium", 1056, id="1K竖图"),
            pytest.param("2K", "1:1", "1440x1440", "high", 2347, id="2K方图不乘系数"),
        ],
    )
    @pytest.mark.parametrize("model", ["gpt-image-2.6-sunburst", "gpt-image-2.6-flare", "relay-image-xyz"])
    def test_estimate_usage_reads_rules_from_the_entry_for_any_model_name(
        self, model: str, image_size: str, aspect_ratio: str, size: str, quality: str, tokens: int
    ) -> None:
        from lib.backends.image_backends.base import ImageRequestFacts
        from lib.backends.image_backends.openai import OpenAIImageBackend

        usage = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(
                model=model,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                reference_count=0,
                entry=_image_entry("gpt-image-2.6-sunburst", _GPT_IMAGE_2_6_BASES, _GPT_IMAGE_2_6_SCALED_BASES),
            )
        )

        assert usage.dimensions == {"resolution": size, "quality": quality}
        assert usage.quantities["image_output_tokens"] == tokens
