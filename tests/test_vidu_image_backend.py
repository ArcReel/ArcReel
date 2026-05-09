"""ViduImageBackend 单元测试 — 重点校验 prompt/aspect_ratio/resolution 白名单兜底逻辑。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.image_backends.vidu import (
    _ASPECT_RATIO_WHITELIST,
    _MAX_REFERENCE_IMAGES,
    _PROMPT_MAX_LEN,
    _RESOLUTION_WHITELIST,
    DEFAULT_MODEL,
    ViduImageBackend,
)
from lib.providers import PROVIDER_VIDU


@pytest.fixture
def output_path(tmp_path: Path) -> Path:
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

    def test_q1_capability_only_i2i(self):
        backend = ViduImageBackend(api_key="test-key", model="viduq1")
        assert ImageCapability.IMAGE_TO_IMAGE in backend.capabilities
        assert ImageCapability.TEXT_TO_IMAGE not in backend.capabilities


class TestWhitelistConfig:
    """避免运行时调用，先把配置常量当 spec 钉死，防止误改。"""

    def test_q1_resolution_only_1080p(self):
        assert _RESOLUTION_WHITELIST["viduq1"] == ["1080p"]

    def test_q2_resolution_includes_1080p_2k_4k(self):
        assert "1080p" in _RESOLUTION_WHITELIST["viduq2"]
        assert "2K" in _RESOLUTION_WHITELIST["viduq2"]
        assert "4K" in _RESOLUTION_WHITELIST["viduq2"]

    def test_q1_aspect_ratio_no_2_3_or_3_2(self):
        # 文档：viduq1 只允许 16:9 9:16 1:1 3:4 4:3
        assert "2:3" not in _ASPECT_RATIO_WHITELIST["viduq1"]
        assert "3:2" not in _ASPECT_RATIO_WHITELIST["viduq1"]

    def test_q2_aspect_ratio_includes_auto(self):
        assert "auto" in _ASPECT_RATIO_WHITELIST["viduq2"]

    def test_max_reference_images_is_seven(self):
        assert _MAX_REFERENCE_IMAGES == 7

    def test_prompt_truncation_limit(self):
        assert _PROMPT_MAX_LEN == 2000


class TestCapabilityMismatchRaises:
    async def test_q1_without_refs_rejects_t2i(self, output_path: Path):
        backend = ViduImageBackend(api_key="test-key", model="viduq1")
        request = ImageGenerationRequest(prompt="hello", output_path=output_path)
        with pytest.raises(ImageCapabilityError):
            await backend.generate(request)

    async def test_q2_with_refs_path_does_not_raise_capability(self, tmp_path: Path, output_path: Path, monkeypatch):
        # 仅校验 capability 检查不会先拦下来；实际生成走 mock。
        # 让 create_vidu_client 抛错是为了快速短路，避免真正发请求。
        from lib.image_backends import vidu as mod

        def _boom(*a, **kw):
            raise RuntimeError("short-circuit")

        monkeypatch.setattr(mod, "create_vidu_client", _boom)
        ref_file = tmp_path / "ref.png"
        ref_file.write_bytes(b"\x89PNG\r\n\x1a\n")
        backend = ViduImageBackend(api_key="test-key", model="viduq2")
        request = ImageGenerationRequest(
            prompt="hello",
            output_path=output_path,
            reference_images=[ReferenceImage(path=str(ref_file))],
        )
        # 不应是 ImageCapabilityError；应是我们注入的 RuntimeError
        with pytest.raises(RuntimeError, match="short-circuit"):
            await backend.generate(request)
