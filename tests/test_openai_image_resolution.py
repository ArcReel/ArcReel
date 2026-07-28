"""测试 OpenAIImageBackend 的「比例优先、清晰度其次」尺寸语义：

- image_size=None → 仍按默认 720P 短边下传精确比例 size（不再省略 size 让上游决定比例）
- image_size=档位 → 短边来自档位、比例来自 aspect_ratio、附 quality
- image_size=自定义 WxH → 剥离自带比例（取 min 当短边），比例仍由 aspect_ratio 决定
- I2I（images.edit）与 T2I 对称下传 size
"""

from unittest.mock import MagicMock

import pytest

from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ReferenceImage
from lib.image_backends.openai import OpenAIImageBackend


def _make_backend():
    backend = OpenAIImageBackend.__new__(OpenAIImageBackend)
    backend._client = MagicMock()
    backend._model = "gpt-image-2"
    # 全能力（默认 mode="both"），让 generate() 的 capability gating 放行 T2I 与 I2I
    backend._capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}
    return backend


def _stub_generate(backend) -> dict:
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data = [type("D", (), {"b64_json": "aGk="})()]

        return FakeResp()

    backend._client.images.generate = fake_generate
    return captured


def _stub_edit(backend) -> dict:
    captured: dict = {}

    async def fake_edit(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data = [type("D", (), {"b64_json": "aGk="})()]

        return FakeResp()

    backend._client.images.edit = fake_edit
    return captured


@pytest.mark.asyncio
async def test_image_size_none_still_sends_ratio_correct_size(tmp_path):
    """image_size=None 时按 720P 兜底下传精确比例 size（修复：旧行为省略 size 丢比例）。"""
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size=None,
    )
    await backend.generate(req)

    assert captured["size"] == "720x1280"
    assert "quality" not in captured  # None 无档位 → 不传 quality


@pytest.mark.asyncio
async def test_image_size_tier_maps_to_short_edge(tmp_path):
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="1K",
    )
    await backend.generate(req)

    assert captured["size"] == "1008x1792"  # 1K 短边 1024 → 9:16 精确
    assert captured["quality"] == "medium"


@pytest.mark.asyncio
async def test_custom_wh_strips_embedded_ratio(tmp_path):
    """自定义 WxH 只贡献 min 当短边，比例仍由 aspect_ratio 决定（不被 16:9 的 1920x1080 带偏）。"""
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="1920x1080",  # 16:9 的值，但项目要 9:16
    )
    await backend.generate(req)

    w, h = (int(x) for x in captured["size"].split("x"))
    assert w * 16 == h * 9  # 精确 9:16，而非输入的 16:9
    assert w < h  # 竖屏
    assert "quality" not in captured  # 自定义值无档位映射


@pytest.mark.asyncio
async def test_noncanonical_tier_casing_still_maps_quality(tmp_path):
    """档位词大小写/空白不规范（如 '2k'）时 size 与 quality 都应解析，不能 size 成功而 quality 丢失。"""
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="2k ",  # 小写 + 尾随空格
    )
    await backend.generate(req)

    assert captured["size"] == "1440x2560"
    assert captured["quality"] == "high"


@pytest.mark.asyncio
async def test_2k_tier(tmp_path):
    backend = _make_backend()
    captured = _stub_generate(backend)

    req = ImageGenerationRequest(
        prompt="hi",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="2K",
    )
    await backend.generate(req)

    assert captured["size"] == "1440x2560"  # 2K 短边 1440
    assert captured["quality"] == "high"


@pytest.mark.asyncio
async def test_i2i_edit_sends_size_symmetric_with_t2i(tmp_path):
    """I2I 路径（images.edit）也下传 size——修复用户实测的 I2I 比例丢失。"""
    backend = _make_backend()
    captured = _stub_edit(backend)

    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)

    req = ImageGenerationRequest(
        prompt="edit",
        output_path=tmp_path / "o.png",
        aspect_ratio="9:16",
        image_size="1K",
        reference_images=[ReferenceImage(path=str(ref))],
    )
    await backend.generate(req)

    assert captured["size"] == "1008x1792"
    assert captured["quality"] == "medium"
