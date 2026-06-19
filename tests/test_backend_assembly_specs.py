"""内置 ProviderSpec 表 + _build_simple 闭包的 sync 构造单测。

镜像 test_custom_provider_factory.py：patch 各 backend 类、手搓 LoadedConfig 信封、
断言 backend 类被以正确构造参数调用。逐 (provider, media) 覆盖简单族 base_url 优先级特例。
"""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from lib.backend_assembly.loaded_config import LoadedConfig
from lib.backend_assembly.specs import (
    PROVIDER_SPEC_REGISTRY,
    _validate_provider_specs,
    get_provider_spec,
)
from lib.config.registry import PROVIDER_REGISTRY


def _loaded(*, credentials: dict, provider_id: str) -> LoadedConfig:
    return LoadedConfig(
        credentials=credentials,
        provider_meta=PROVIDER_REGISTRY.get(provider_id),
        rate_limiter=None,
    )


class TestBuildSimpleBaseUrlPriority:
    """简单族 base_url 优先级：用户显式 > registry default > 不传。"""

    @patch("lib.image_backends.registry.create_backend")
    def test_ark_image_falls_back_to_registry_default(self, mock_create):
        spec = get_provider_spec("ark", "image")
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="ark")
        spec.build_backend(config, "doubao-seed-2-0-pro-260215")
        mock_create.assert_called_once_with(
            "ark",
            api_key="sk-test",
            model="doubao-seed-2-0-pro-260215",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
        )

    @patch("lib.image_backends.registry.create_backend")
    def test_user_base_url_wins_over_registry_default(self, mock_create):
        spec = get_provider_spec("ark", "image")
        config = _loaded(
            credentials={"api_key": "sk-test", "base_url": "https://custom.example.com/v3"},
            provider_id="ark",
        )
        spec.build_backend(config, "model-x")
        mock_create.assert_called_once_with(
            "ark", api_key="sk-test", model="model-x", base_url="https://custom.example.com/v3"
        )

    @patch("lib.video_backends.registry.create_backend")
    def test_ark_agent_plan_uses_own_plan_base_url(self, mock_create):
        # ark-agent-plan 媒体侧复用 Ark backend，但 registry default 是独立的 /api/plan/v3
        # （非 ark 的 /api/v3）——回归保护：迁移前经简单族构造即取此值，新缝须一致。
        spec = get_provider_spec("ark-agent-plan", "video")
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="ark-agent-plan")
        spec.build_backend(config, "doubao-seedance-2.0")
        mock_create.assert_called_once_with(
            "ark-agent-plan",
            api_key="sk-test",
            model="doubao-seedance-2.0",
            base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        )

    @patch("lib.image_backends.registry.create_backend")
    def test_grok_image_no_default_no_user_omits_base_url(self, mock_create):
        # grok 无 registry default 且用户未配 → 不传 base_url（grok backend 不接受该参数）
        spec = get_provider_spec("grok", "image")
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="grok")
        spec.build_backend(config, "grok-2-image")
        mock_create.assert_called_once_with("grok", api_key="sk-test", model="grok-2-image")

    @patch("lib.image_backends.registry.create_backend")
    def test_missing_api_key_omitted_so_sdk_env_fallback_survives(self, mock_create):
        # 用户未配 api_key → 不传 api_key（而非传 None）：让 backend 各自决定环境变量兜底
        # （OpenAI SDK 读 OPENAI_API_KEY）或 fail-loud；显式 None 会覆盖兜底。
        spec = get_provider_spec("openai", "image")
        config = _loaded(credentials={}, provider_id="openai")
        spec.build_backend(config, "gpt-image-1")
        mock_create.assert_called_once_with("openai", model="gpt-image-1")


class TestMediaRegistryRouting:
    """_build_simple 按 media_type 选对应 registry 的 create_backend（唯一分支逻辑）。"""

    @patch("lib.video_backends.registry.create_backend")
    def test_dashscope_video_uses_video_registry_and_default(self, mock_create):
        spec = get_provider_spec("dashscope", "video")
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        spec.build_backend(config, "wan2.7-r2v")
        mock_create.assert_called_once_with(
            "dashscope", api_key="sk-test", model="wan2.7-r2v", base_url="https://dashscope.aliyuncs.com"
        )

    @patch("lib.audio_backends.registry.create_backend")
    def test_dashscope_audio_uses_audio_registry(self, mock_create):
        spec = get_provider_spec("dashscope", "audio")
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        spec.build_backend(config, "qwen3-tts-flash")
        mock_create.assert_called_once_with(
            "dashscope", api_key="sk-test", model="qwen3-tts-flash", base_url="https://dashscope.aliyuncs.com"
        )


class TestRegistryShape:
    def test_unknown_provider_media_fails_loud(self):
        with pytest.raises(ValueError, match="no builtin ProviderSpec"):
            get_provider_spec("ark", "audio")  # ark 无 audio backend，未登记

    def test_audio_only_dashscope_registered(self):
        audio_keys = {k for k in PROVIDER_SPEC_REGISTRY if k[1] == "audio"}
        assert audio_keys == {("dashscope", "audio")}

    def test_simple_family_image_video_complete(self):
        for provider in ("ark", "ark-agent-plan", "grok", "openai", "vidu", "dashscope", "minimax"):
            assert (provider, "image") in PROVIDER_SPEC_REGISTRY
            assert (provider, "video") in PROVIDER_SPEC_REGISTRY


class TestValidateProviderSpecs:
    """import 期不变式：build 可调用、键与 spec 字段一致、media_type 合法。misconfig fail-fast。"""

    def test_passes_on_real_registry(self):
        _validate_provider_specs()  # 真表不抛

    def test_non_callable_build_rejected(self, monkeypatch: pytest.MonkeyPatch):
        bad = dataclasses.replace(PROVIDER_SPEC_REGISTRY[("ark", "image")], build_backend="not-callable")
        monkeypatch.setitem(PROVIDER_SPEC_REGISTRY, ("ark", "image"), bad)
        with pytest.raises(ValueError, match="non-callable build_backend"):
            _validate_provider_specs()

    def test_key_field_mismatch_rejected(self, monkeypatch: pytest.MonkeyPatch):
        # spec 内 provider_id/media_type 与字典键漂移 → fail-fast
        bad = dataclasses.replace(PROVIDER_SPEC_REGISTRY[("ark", "image")], provider_id="drifted")
        monkeypatch.setitem(PROVIDER_SPEC_REGISTRY, ("ark", "image"), bad)
        with pytest.raises(ValueError, match="key .* does not match spec"):
            _validate_provider_specs()

    def test_registry_backend_names_are_registered(self):
        """ADR 0039：registry 名都在媒体后端 registry 里 —— 归单测（import 全部后端无碍），不进 import 期。"""
        from lib.audio_backends import get_registered_backends as audio_names
        from lib.image_backends import get_registered_backends as image_names
        from lib.video_backends import get_registered_backends as video_names

        registered = {"image": set(image_names()), "video": set(video_names()), "audio": set(audio_names())}
        for (_provider, media), spec in PROVIDER_SPEC_REGISTRY.items():
            assert spec.registry_backend in registered[media], (
                f"{spec.registry_backend!r} 未注册到 {media} backend registry"
            )
