"""create_custom_backend(provider, model_id, endpoint) 单元测试。

endpoints 模块内的真实后端类换成记录型工厂：工厂的产出就是「用哪个后端类、什么构造参数」，
断言落在整条构造记录上（能抓多传/漏传参数），delegate 仍是可用对象供包装层继续构造。
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.factory import create_custom_backend

# endpoints 模块里被 build_backend 闭包直接构造的全部后端类。
_ENDPOINT_BACKEND_CLASSES = (
    "ArkVideoBackend",
    "DashScopeImageBackend",
    "DashScopeVideoBackend",
    "DeclarativeVideoBackend",
    "GeminiImageBackend",
    "GeminiTextBackend",
    "KlingImageBackend",
    "KlingVideoBackend",
    "MiniMaxImageBackend",
    "OpenAIAudioBackend",
    "OpenAIImageBackend",
    "OpenAITextBackend",
    "OpenAIVideoBackend",
    "ViduVideoBackend",
)


@contextmanager
def _endpoint_backends() -> Generator[list[dict[str, Any]]]:
    """endpoints 模块各后端类的构造记录器：记类名与构造参数，不建 SDK 客户端。"""
    records: list[dict[str, Any]] = []

    def _recorder(cls_name: str):
        def _build(**kwargs: Any) -> Any:
            records.append({"backend": cls_name, "kwargs": kwargs})
            return MagicMock()

        return _build

    with ExitStack() as stack:
        for cls_name in _ENDPOINT_BACKEND_CLASSES:
            stack.enter_context(patch(f"lib.custom_provider.endpoints.{cls_name}", _recorder(cls_name)))
        yield records


def _make_provider(*, base_url: str = "https://api.example.com/v1", api_key: str = "sk-test") -> MagicMock:
    p = MagicMock()
    p.base_url = base_url
    p.api_key = api_key
    p.provider_id = "custom-42"
    return p


def _built(provider: MagicMock, model_id: str, endpoint: str, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
    """跑一次工厂，返回 (包装层结果, 唯一一条 delegate 构造记录)。"""
    with _endpoint_backends() as records:
        result = create_custom_backend(provider=provider, model_id=model_id, endpoint=endpoint, **kwargs)
    assert len(records) == 1
    return result, records[0]


class TestEndpointDispatch:
    def test_openai_chat(self):
        # host-only base_url：openai 端点补 /v1 的接线在此覆盖
        provider = _make_provider(base_url="https://api.example.com")
        result, built = _built(provider, "gpt-4o", "openai-chat")
        assert isinstance(result, CustomTextBackend)
        assert result.model == "gpt-4o"
        assert built == {
            "backend": "OpenAITextBackend",
            "kwargs": {"api_key": "sk-test", "base_url": "https://api.example.com/v1", "model": "gpt-4o"},
        }

    def test_gemini_generate(self):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        _result, built = _built(provider, "gemini-2.5-flash", "gemini-generate")
        assert built == {
            "backend": "GeminiTextBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://generativelanguage.googleapis.com/",
                "model": "gemini-2.5-flash",
            },
        }

    def test_openai_images(self):
        result, built = _built(_make_provider(), "dall-e-3", "openai-images")
        assert isinstance(result, CustomImageBackend)
        assert built == {
            "backend": "OpenAIImageBackend",
            "kwargs": {"api_key": "sk-test", "base_url": "https://api.example.com/v1", "model": "dall-e-3"},
        }

    def test_gemini_image(self):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        _result, built = _built(provider, "imagen-4", "gemini-image")
        assert built == {
            "backend": "GeminiImageBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://generativelanguage.googleapis.com/",
                "image_model": "imagen-4",
            },
        }

    def test_openai_video(self):
        result, built = _built(_make_provider(), "sora-2", "openai-video")
        assert isinstance(result, CustomVideoBackend)
        assert built == {
            "backend": "OpenAIVideoBackend",
            "kwargs": {"api_key": "sk-test", "base_url": "https://api.example.com/v1", "model": "sora-2"},
        }

    def test_newapi_video(self):
        _result, built = _built(_make_provider(), "kling-v2", "newapi-video")
        assert built["backend"] == "DeclarativeVideoBackend"
        assert built["kwargs"]["model"] == "kling-v2"
        assert built["kwargs"]["provider"] == "custom-42"

    def test_v2_video_generations(self):
        provider = _make_provider(base_url="https://api.aimlapi.com")
        result, built = _built(provider, "bytedance/seedance-1-0-lite-i2v", "v2-video-generations")
        assert isinstance(result, CustomVideoBackend)
        assert built["backend"] == "DeclarativeVideoBackend"
        assert built["kwargs"]["base_url"] == "https://api.aimlapi.com"
        assert built["kwargs"]["model"] == "bytedance/seedance-1-0-lite-i2v"

    def test_ark_seedance(self):
        provider = _make_provider(base_url="https://relay.example.com")
        result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance")
        assert isinstance(result, CustomVideoBackend)
        # 仅 host → 补全 ark 协议挂载路径 /api/v3
        assert built == {
            "backend": "ArkVideoBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/api/v3",
                "model": "doubao-seedance-2-0",
            },
        }

    def test_vidu_video(self):
        provider = _make_provider(base_url="https://relay.example.com")
        result, built = _built(provider, "viduq3-turbo", "vidu-video")
        assert isinstance(result, CustomVideoBackend)
        # 仅 host → 补全 vidu 协议挂载路径 /ent/v2
        assert built == {
            "backend": "ViduVideoBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/ent/v2",
                "model": "viduq3-turbo",
            },
        }

    def test_openai_tts(self):
        result, built = _built(_make_provider(), "tts-1", "openai-tts")
        assert isinstance(result, CustomAudioBackend)
        assert result.name == "custom-42"
        assert result.model == "tts-1"
        # provider_name 让 delegate 记账/日志归因到真实 provider 而非内置 openai
        assert built == {
            "backend": "OpenAIAudioBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "model": "tts-1",
                "provider_name": "custom-42",
            },
        }

    def test_openai_tts_appends_v1(self):
        provider = _make_provider(base_url="https://relay.example.com")
        _result, built = _built(provider, "speech-1.5", "openai-tts")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/v1"

    def test_minimax_image(self):
        provider = _make_provider(base_url="https://api.minimaxi.com/v1")
        result, built = _built(provider, "image-01", "minimax-image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "image-01"
        # base_url 原样下传，归一化（host→{host}/v1）由 MiniMaxImageBackend 内部处理
        assert built == {
            "backend": "MiniMaxImageBackend",
            "kwargs": {"api_key": "sk-test", "base_url": "https://api.minimaxi.com/v1", "model": "image-01"},
        }

    def test_minimax_video(self):
        provider = _make_provider(base_url="https://api.minimaxi.com/v1")
        result, built = _built(provider, "MiniMax-Hailuo-2.3", "minimax-hailuo-v1")
        assert isinstance(result, CustomVideoBackend)
        assert result.model == "MiniMax-Hailuo-2.3"
        assert built["backend"] == "DeclarativeVideoBackend"
        assert built["kwargs"]["base_url"] == "https://api.minimaxi.com/v1"

    def test_kling_image(self):
        provider = _make_provider(base_url="https://relay.example.com/v1")
        result, built = _built(provider, "kling-image-o1", "kling-image")
        assert isinstance(result, CustomImageBackend)
        assert result.model == "kling-image-o1"
        # bearer 模式：静态 api_key 旁路 JWT；显式 /v1 路径原样信任；原生 model_name 透传
        assert built == {
            "backend": "KlingImageBackend",
            "kwargs": {
                "auth_mode": "bearer",
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/v1",
                "model": "kling-image-o1",
            },
        }

    def test_kling_image_host_only_mounts_v1(self):
        provider = _make_provider(base_url="https://relay.example.com")
        _result, built = _built(provider, "kling-image-o1", "kling-image")
        # 仅 host → 补全可灵协议挂载路径 /v1
        assert built["kwargs"]["base_url"] == "https://relay.example.com/v1"

    def test_kling_video(self):
        provider = _make_provider(base_url="https://relay.example.com/v1")
        result, built = _built(provider, "kling-v2-5-turbo", "kling-video")
        assert isinstance(result, CustomVideoBackend)
        assert result.model == "kling-v2-5-turbo"
        assert built == {
            "backend": "KlingVideoBackend",
            "kwargs": {
                "auth_mode": "bearer",
                "api_key": "sk-test",
                "base_url": "https://relay.example.com/v1",
                "model": "kling-v2-5-turbo",
            },
        }

    def test_kling_video_host_only_mounts_v1(self):
        provider = _make_provider(base_url="relay.example.com")
        _result, built = _built(provider, "kling-v3", "kling-video")
        # 纯域名（无 scheme）→ 补 https:// 再挂载 /v1
        assert built["kwargs"]["base_url"] == "https://relay.example.com/v1"

    def test_openai_images_generations(self):
        result, built = _built(_make_provider(), "dall-e-3", "openai-images-generations")
        assert isinstance(result, CustomImageBackend)
        assert built == {
            "backend": "OpenAIImageBackend",
            "kwargs": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com/v1",
                "model": "dall-e-3",
                "mode": "generations_only",
            },
        }

    def test_openai_images_edits(self):
        result, built = _built(_make_provider(), "dall-e-3", "openai-images-edits")
        assert isinstance(result, CustomImageBackend)
        assert built["kwargs"]["mode"] == "edits_only"


class TestUrlNormalization:
    """挂载路径补全（_ensure_url_path_suffix）：仅此处覆盖。openai 补 /v1、google 剥版本段的
    纯归一化行为由 tests/unit/lib/config/test_normalize_base_url.py 覆盖，不在此重复。"""

    def test_ark_explicit_path_passthrough(self):
        """已带显式路径（/api/v3）→ 原样信任，不重复叠加。"""
        provider = _make_provider(base_url="https://relay.example.com/api/v3")
        _result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/api/v3"

    def test_ark_mounted_base_url_appends_api_v3(self):
        provider = _make_provider(base_url="https://relay.example.com/seedance")
        _result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/seedance/api/v3"

    def test_vidu_explicit_path_passthrough(self):
        provider = _make_provider(base_url="https://api.vidu.cn/ent/v2")
        _result, built = _built(provider, "viduq3-turbo", "vidu-video")
        assert built["kwargs"]["base_url"] == "https://api.vidu.cn/ent/v2"

    def test_ark_host_only_no_scheme(self):
        """纯域名（无 scheme）→ 补 https:// 再挂载 /api/v3。"""
        provider = _make_provider(base_url="relay.example.com")
        _result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/api/v3"

    def test_vidu_host_only_no_scheme(self):
        provider = _make_provider(base_url="relay.example.com")
        _result, built = _built(provider, "viduq3-turbo", "vidu-video")
        assert built["kwargs"]["base_url"] == "https://relay.example.com/ent/v2"

    def test_ark_empty_base_url_normalizes_to_none(self):
        """空 base_url → _ensure_url_path_suffix 归一化为 None 下传（不强行补挂载路径）。"""
        provider = _make_provider(base_url="")
        _result, built = _built(provider, "doubao-seedance-2-0", "ark-seedance")
        assert built["kwargs"]["base_url"] is None

    def test_vidu_empty_base_url_normalizes_to_none(self):
        provider = _make_provider(base_url="")
        _result, built = _built(provider, "viduq3-turbo", "vidu-video")
        assert built["kwargs"]["base_url"] is None


class TestErrors:
    def test_unknown_endpoint(self):
        provider = _make_provider()
        with pytest.raises(ValueError, match="unknown endpoint"):
            create_custom_backend(provider=provider, model_id="claude-4", endpoint="anthropic-messages")

    def test_v2_empty_base_url_raises(self):
        """v2-video-generations 强制要求 base_url（无默认 host），空值 fail-loud。"""
        provider = _make_provider(base_url="")
        with pytest.raises(ValueError, match="需要 base_url"):
            create_custom_backend(provider=provider, model_id="some-model", endpoint="v2-video-generations")


class TestVideoEndpointRecorded:
    """工厂把构造 endpoint 记进 video 包装层，续跑据此比对协议（`docs/adr/0054`）。"""

    def test_video_backend_records_endpoint(self):
        result, _built_record = _built(_make_provider(), "sora-2", "openai-video")
        assert isinstance(result, CustomVideoBackend)
        assert result.endpoint == "openai-video"

    def test_endpoint_survives_capability_injection(self):
        """能力注入返回新实例，endpoint 不能在链式构造中丢失。"""
        result, _built_record = _built(
            _make_provider(),
            "MiniMax-Hailuo-02",
            "minimax-hailuo-v1",
            capability_overrides={"last_frame": True},
        )
        assert isinstance(result, CustomVideoBackend)
        assert result.endpoint == "minimax-hailuo-v1"
