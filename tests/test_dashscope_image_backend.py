"""DashScopeImageBackend 单元测试（mock httpx，同步端点）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.providers import PROVIDER_DASHSCOPE


def _img_response(url: str = "https://x/out.png") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"output": {"choices": [{"message": {"content": [{"image": url}]}}]}}
    return resp


def _mock_client(resp: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _make_ref(tmp_path: Path, name: str) -> ReferenceImage:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\nfake")
    return ReferenceImage(path=str(p))


def _patches(client: AsyncMock, download: AsyncMock):
    return (
        patch("httpx.AsyncClient", return_value=client),
        patch("lib.image_backends.dashscope.download_image_to_path", download),
    )


class TestCapabilities:
    def test_qwen_image_20_t2i_and_i2i(self):
        from lib.image_backends.dashscope import DashScopeImageBackend

        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        assert b.name == PROVIDER_DASHSCOPE
        assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def test_edit_models_i2i_only(self):
        from lib.image_backends.dashscope import DashScopeImageBackend

        for model in ("qwen-image-edit", "qwen-image-edit-plus", "qwen-image-edit-max"):
            b = DashScopeImageBackend(api_key="sk", model=model)
            assert b.capabilities == {ImageCapability.IMAGE_TO_IMAGE}

    def test_wan_image_t2i_and_i2i(self):
        from lib.image_backends.dashscope import DashScopeImageBackend

        b = DashScopeImageBackend(api_key="sk", model="wan2.7-image-pro")
        assert b.capabilities == {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}


class TestTextToImage:
    async def test_t2i_content_text_only(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0", base_url="https://dashscope.aliyuncs.com")
            result = await b.generate(ImageGenerationRequest(prompt="a fox", output_path=tmp_path / "o.png"))

        body = client.post.call_args.kwargs["json"]
        content = body["input"]["messages"][0]["content"]
        assert content == [{"text": "a fox"}]
        # qwen 系按默认 aspect_ratio=9:16 选授权像素档（不再一律 1:1 方图）
        assert body["parameters"]["size"] == "1536*2688"
        assert body["parameters"]["n"] == 1
        assert body["parameters"]["watermark"] is False
        assert body["parameters"]["prompt_extend"] is False
        # 端点正确（host 派生 /api/v1 + 路径）
        assert client.post.call_args.args[0].endswith("/api/v1/services/aigc/multimodal-generation/generation")
        assert client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk"
        assert "X-DashScope-Async" not in client.post.call_args.kwargs["headers"]
        assert result.provider == PROVIDER_DASHSCOPE
        assert result.image_uri == "https://x/out.png"
        download.assert_called_once()

    async def test_wan_default_size_follows_aspect(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="wan2.7-image")
            # 默认 aspect_ratio=9:16，wan 像素方式按比例选值（满足 wan 总像素/比例约束）
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png"))

        assert client.post.call_args.kwargs["json"]["parameters"]["size"] == "1536*2688"

    async def test_explicit_tier_translated_to_aspect_pixels(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="wan2.7-image")
            # 档位词「2K」按比例换算成显式像素，绝不原样下传（否则 wan 文生图会被强制方图）
            await b.generate(
                ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", aspect_ratio="9:16", image_size="2K")
            )

        assert client.post.call_args.kwargs["json"]["parameters"]["size"] == "1536*2688"

    async def test_explicit_pixel_size_honored(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="wan2.7-image")
            # caller 显式给像素值时原样 honor（比例已由 caller 决定）
            await b.generate(
                ImageGenerationRequest(
                    prompt="x", output_path=tmp_path / "o.png", aspect_ratio="9:16", image_size="1920*1080"
                )
            )

        assert client.post.call_args.kwargs["json"]["parameters"]["size"] == "1920*1080"

    async def test_low_tier_translated_to_aspect_pixels(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="wan2.7-image")
            # 1K 档同样按比例换算（2K 等比 ×0.5）
            await b.generate(
                ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", aspect_ratio="16:9", image_size="1K")
            )

        assert client.post.call_args.kwargs["json"]["parameters"]["size"] == "1344*768"

    async def test_landscape_aspect_picks_wide_size(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            await b.generate(ImageGenerationRequest(prompt="x", output_path=tmp_path / "o.png", aspect_ratio="16:9"))

        assert client.post.call_args.kwargs["json"]["parameters"]["size"] == "2688*1536"


class TestImageToImage:
    async def test_i2i_content_with_images(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        ref = _make_ref(tmp_path, "ref.png")
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            await b.generate(
                ImageGenerationRequest(prompt="edit it", output_path=tmp_path / "o.png", reference_images=[ref])
            )

        content = client.post.call_args.kwargs["json"]["input"]["messages"][0]["content"]
        assert content[0]["image"].startswith("data:image/png;base64,")
        assert content[-1] == {"text": "edit it"}

    async def test_qwen_ref_limit_3(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        refs = [_make_ref(tmp_path, f"r{i}.png") for i in range(5)]
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=refs))

        content = client.post.call_args.kwargs["json"]["input"]["messages"][0]["content"]
        images = [c for c in content if "image" in c]
        assert len(images) == 3  # qwen 上限裁剪

    async def test_wan_ref_limit_9(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        refs = [_make_ref(tmp_path, f"r{i}.png") for i in range(11)]
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="wan2.7-image-pro")
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=refs))

        content = client.post.call_args.kwargs["json"]["input"]["messages"][0]["content"]
        images = [c for c in content if "image" in c]
        assert len(images) == 9


class TestCapabilityGating:
    async def test_t2i_on_i2i_only_raises(self, tmp_path: Path):
        from lib.image_backends.dashscope import DashScopeImageBackend

        b = DashScopeImageBackend(api_key="sk", model="qwen-image-edit-plus")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        assert ei.value.code == "image_endpoint_mismatch_no_t2i"

    async def test_wan_pro_4k_i2i_raises(self, tmp_path: Path):
        from lib.image_backends.dashscope import DashScopeImageBackend

        ref = _make_ref(tmp_path, "ref.png")
        b = DashScopeImageBackend(api_key="sk", model="wan2.7-image-pro")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", image_size="4K", reference_images=[ref]
                )
            )
        assert ei.value.code == "image_dashscope_4k_t2i_only"

    async def test_wan_pro_4k_t2i_allowed(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="wan2.7-image-pro")
            # 4K 在 pro 文生图允许，但仍按比例换算成显式像素（4K 预算 = 2K 等比 ×2），不下传「4K」档位
            await b.generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", aspect_ratio="16:9", image_size="4K")
            )
        assert client.post.call_args.kwargs["json"]["parameters"]["size"] == "5376*3072"

    async def test_wan_non_pro_4k_t2i_raises(self, tmp_path: Path):
        from lib.image_backends.dashscope import DashScopeImageBackend

        # 非 pro 的 wan2.7-image 完全不支持 4K（即便文生图），须拒绝而非透传给上游
        b = DashScopeImageBackend(api_key="sk", model="wan2.7-image")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", image_size="4K"))
        assert ei.value.code == "image_dashscope_4k_t2i_only"

    async def test_all_refs_missing_raises(self, tmp_path: Path):
        from lib.image_backends.dashscope import DashScopeImageBackend

        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.png",
                    reference_images=[ReferenceImage(path=str(tmp_path / "nope.png"))],
                )
            )
        # 模型支持 i2i，只是参考图不可读 → 用准确码而非"模型不支持 i2i"
        assert ei.value.code == "image_reference_images_unreadable"

    async def test_empty_ref_path_treated_as_missing(self, tmp_path: Path):
        from lib.image_backends.dashscope import DashScopeImageBackend

        # 空串路径 Path("").exists() 会误判为 True；用 is_file 拦掉，避免读到目录崩溃
        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        with pytest.raises(ImageCapabilityError) as ei:
            await b.generate(
                ImageGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.png", reference_images=[ReferenceImage(path="")]
                )
            )
        assert ei.value.code == "image_reference_images_unreadable"

    async def test_oversized_numeric_t2i_raises(self, tmp_path: Path):
        from lib.image_backends.dashscope import DashScopeImageBackend

        # 超 2048×2048 总像素预算的像素值（文档 4K=4096×4096，及其它超预算写法/分隔符）
        # 须被门控拦截，不能因数字写法绕过；非 pro 完全不支持
        b = DashScopeImageBackend(api_key="sk", model="wan2.7-image")
        for size in ("4096*4096", "4096×2160", "3000*3000"):
            with pytest.raises(ImageCapabilityError) as ei:
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", image_size=size))
            assert ei.value.code == "image_dashscope_4k_t2i_only"

    async def test_narrow_size_within_budget_not_gated(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            # 窄幅尺寸总像素在 2048×2048 预算内（4096*512=2.1M < 4.19M），合法、不应被门控误拒
            # （按"任意维度>2048"判会错杀这类比例尺寸，故门控用总像素而非单维阈值）
            b = DashScopeImageBackend(api_key="sk", model="wan2.7-image")
            await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", image_size="4096*512"))
        assert client.post.call_args.kwargs["json"]["parameters"]["size"] == "4096*512"

    async def test_all_refs_unreadable_oserror_raises(self, tmp_path: Path):
        from lib.image_backends.dashscope import DashScopeImageBackend

        # 文件存在但 read 时抛 OSError（权限/IO）→ 全部跳过后报准确码，不炸成 500
        ref = _make_ref(tmp_path, "ref.png")
        b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
        with patch("lib.image_backends.dashscope.image_to_data_uri", side_effect=OSError("permission denied")):
            with pytest.raises(ImageCapabilityError) as ei:
                await b.generate(
                    ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=[ref])
                )
        assert ei.value.code == "image_reference_images_unreadable"

    async def test_partial_unreadable_refs_fail_loud(self, tmp_path: Path):
        client = _mock_client(_img_response())
        download = AsyncMock()
        r1, r2 = _make_ref(tmp_path, "a.png"), _make_ref(tmp_path, "b.png")

        def fake_uri(p: Path) -> str:
            if p.name == "a.png":
                raise OSError("io error")
            return "data:image/png;base64,OK"

        p1, p2 = _patches(client, download)
        with p1, p2, patch("lib.image_backends.dashscope.image_to_data_uri", side_effect=fake_uri):
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            # fail-loud：a.png 不可读即中止，不静默用 b.png 的子集生成；报错列出不可读文件名
            with pytest.raises(ImageCapabilityError) as ei:
                await b.generate(
                    ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", reference_images=[r1, r2])
                )
        assert ei.value.code == "image_reference_images_unreadable"
        assert "a.png" in ei.value.params["names"]
        client.post.assert_not_called()


class TestErrorResponse:
    async def test_http_error_raises(self, tmp_path: Path):
        resp = MagicMock()
        resp.status_code = 400
        resp.text = "bad request"
        client = _mock_client(resp)
        download = AsyncMock()
        p1, p2 = _patches(client, download)
        with p1, p2:
            from lib.image_backends.dashscope import DashScopeImageBackend

            b = DashScopeImageBackend(api_key="sk", model="qwen-image-2.0")
            with pytest.raises(RuntimeError, match="400"):
                await b.generate(ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png"))
        download.assert_not_called()
