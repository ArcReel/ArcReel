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


class TestGeminiSpec:
    """gemini 特例族：backend_type 按 provider_id 分叉（aistudio/vertex 各一行），image 设 base_url /
    video 不设（非对称提升为两条表行），注入共享 rate_limiter，image_model/video_model 命名差异。
    api_key 与 base_url 无条件透传（含 None）：与迁移前命令式分支一致，由 backend 内 resolve_gemini_api_key
    / normalize_base_url 处理 None（读环境变量 / 省略）。"""

    @patch("lib.image_backends.registry.create_backend")
    def test_aistudio_image_sets_base_url_and_image_model(self, mock_create):
        spec = get_provider_spec("gemini-aistudio", "image")
        assert spec.registry_backend == "gemini"
        limiter = object()
        config = LoadedConfig(
            credentials={"api_key": "sk-aistudio", "base_url": "https://custom.example.com"},
            provider_meta=PROVIDER_REGISTRY.get("gemini-aistudio"),
            rate_limiter=limiter,
        )
        spec.build_backend(config, "gemini-3.1-flash-image-preview")
        mock_create.assert_called_once_with(
            "gemini",
            backend_type="aistudio",
            api_key="sk-aistudio",
            base_url="https://custom.example.com",
            rate_limiter=limiter,
            image_model="gemini-3.1-flash-image-preview",
        )

    @patch("lib.image_backends.registry.create_backend")
    def test_vertex_image_backend_type_vertex(self, mock_create):
        spec = get_provider_spec("gemini-vertex", "image")
        config = LoadedConfig(
            credentials={"api_key": None, "base_url": None},
            provider_meta=PROVIDER_REGISTRY.get("gemini-vertex"),
            rate_limiter=None,
        )
        spec.build_backend(config, None)
        # vertex 无 api_key/base_url：仍无条件透传 None（backend 内回落凭证文件 / 省略 base_url）
        mock_create.assert_called_once_with(
            "gemini",
            backend_type="vertex",
            api_key=None,
            base_url=None,
            rate_limiter=None,
            image_model=None,
        )

    @patch("lib.video_backends.registry.create_backend")
    def test_aistudio_video_omits_base_url_uses_video_model(self, mock_create):
        spec = get_provider_spec("gemini-aistudio", "video")
        assert spec.registry_backend == "gemini"
        limiter = object()
        config = LoadedConfig(
            credentials={"api_key": "sk-aistudio", "base_url": "https://ignored.example.com"},
            provider_meta=PROVIDER_REGISTRY.get("gemini-aistudio"),
            rate_limiter=limiter,
        )
        spec.build_backend(config, "veo-3.1-lite-generate-preview")
        # video 非对称：不传 base_url（即使 credentials 含），命名参数是 video_model 不是 image_model
        mock_create.assert_called_once_with(
            "gemini",
            backend_type="aistudio",
            api_key="sk-aistudio",
            rate_limiter=limiter,
            video_model="veo-3.1-lite-generate-preview",
        )

    @patch("lib.video_backends.registry.create_backend")
    def test_vertex_video_backend_type_vertex(self, mock_create):
        spec = get_provider_spec("gemini-vertex", "video")
        config = LoadedConfig(
            credentials={"api_key": None},
            provider_meta=PROVIDER_REGISTRY.get("gemini-vertex"),
            rate_limiter=None,
        )
        spec.build_backend(config, "veo-3.1-generate-preview")
        mock_create.assert_called_once_with(
            "gemini",
            backend_type="vertex",
            api_key=None,
            rate_limiter=None,
            video_model="veo-3.1-generate-preview",
        )

    def test_bare_gemini_not_registered(self):
        # 裸 "gemini"（无 aistudio/vertex 后缀）是死路径：resolver 只产出带后缀 id。
        # 按 ADR 0039 fail-loud，不为死路径登记兜底行。
        assert ("gemini", "image") not in PROVIDER_SPEC_REGISTRY
        assert ("gemini", "video") not in PROVIDER_SPEC_REGISTRY


class TestKlingSpec:
    """kling 特例族：JWT 双 secret（access_key + secret_key 按 ADR 0037 列名直取）、auth_mode=jwt、
    image 侧 api_model_name 解耦（两栖别名键读 registry api_model_name）、base_url 兜底（db > registry default）。
    video backend 不接受 api_model_name —— 非对称，video 闭包不传。"""

    @patch("lib.image_backends.registry.create_backend")
    def test_image_dual_secret_and_jwt(self, mock_create):
        spec = get_provider_spec("kling", "image")
        assert spec.registry_backend == "kling"
        config = LoadedConfig(
            credentials={"access_key": "ak-1", "secret_key": "sk-1"},
            provider_meta=PROVIDER_REGISTRY.get("kling"),
            rate_limiter=None,
        )
        spec.build_backend(config, "kling-image-o1")
        mock_create.assert_called_once_with(
            "kling",
            auth_mode="jwt",
            access_key="ak-1",
            secret_key="sk-1",
            model="kling-image-o1",
            base_url="https://api.klingai.com/v1",
        )

    @patch("lib.image_backends.registry.create_backend")
    def test_image_api_model_name_decoupled_for_amphibious_alias(self, mock_create):
        # 两栖别名键 kling-v3-omni-image 的 registry api_model_name 是 kling-v3-omni（发真实 API 名）。
        spec = get_provider_spec("kling", "image")
        config = LoadedConfig(
            credentials={"access_key": "ak-1", "secret_key": "sk-1"},
            provider_meta=PROVIDER_REGISTRY.get("kling"),
            rate_limiter=None,
        )
        spec.build_backend(config, "kling-v3-omni-image")
        mock_create.assert_called_once_with(
            "kling",
            auth_mode="jwt",
            access_key="ak-1",
            secret_key="sk-1",
            model="kling-v3-omni-image",
            api_model_name="kling-v3-omni",
            base_url="https://api.klingai.com/v1",
        )

    @patch("lib.image_backends.registry.create_backend")
    def test_image_user_base_url_wins_over_registry_default(self, mock_create):
        spec = get_provider_spec("kling", "image")
        config = LoadedConfig(
            credentials={"access_key": "ak-1", "secret_key": "sk-1", "base_url": "https://relay.example.com"},
            provider_meta=PROVIDER_REGISTRY.get("kling"),
            rate_limiter=None,
        )
        spec.build_backend(config, "kling-image-o1")
        mock_create.assert_called_once_with(
            "kling",
            auth_mode="jwt",
            access_key="ak-1",
            secret_key="sk-1",
            model="kling-image-o1",
            base_url="https://relay.example.com",
        )

    @patch("lib.video_backends.registry.create_backend")
    def test_video_dual_secret_no_api_model_name(self, mock_create):
        spec = get_provider_spec("kling", "video")
        assert spec.registry_backend == "kling"
        config = LoadedConfig(
            credentials={"access_key": "ak-1", "secret_key": "sk-1"},
            provider_meta=PROVIDER_REGISTRY.get("kling"),
            rate_limiter=None,
        )
        spec.build_backend(config, "kling-v3")
        # video backend 不接受 api_model_name：即使 model 是别名也不传该参数（迁移前 video 分支即不设）
        mock_create.assert_called_once_with(
            "kling",
            auth_mode="jwt",
            access_key="ak-1",
            secret_key="sk-1",
            model="kling-v3",
            base_url="https://api.klingai.com/v1",
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
