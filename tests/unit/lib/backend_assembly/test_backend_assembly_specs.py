"""内置 ProviderSpec 表 + _build_simple 闭包的 sync 构造单测。

装配层的产出是「往哪个 media registry、用什么后端名、什么构造参数建后端」：用记录型工厂
替下四个 registry 的真实后端，逐 (provider, media) 断言整条构造记录，覆盖简单族 base_url
优先级、gemini/kling 特例族的分叉与参数解耦。
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from lib.backend_assembly.loaded_config import LoadedConfig
from lib.backend_assembly.specs import (
    PROVIDER_SPEC_REGISTRY,
    ProviderSpec,
    _validate_provider_specs,
    get_provider_spec,
)
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
from tests.fakes import captured_backend_construction


def _loaded(*, credentials: dict, provider_id: str) -> LoadedConfig:
    return LoadedConfig(
        credentials=credentials,
        provider_meta=PROVIDER_REGISTRY.get(provider_id),
        rate_limiter=None,
    )


def _built(spec: ProviderSpec, config: LoadedConfig, model: str | None) -> dict[str, Any]:
    """跑一次装配，返回唯一一条构造记录（media / backend 名 / 构造参数）。"""
    with captured_backend_construction() as records:
        spec.build_backend(config, model)
    assert len(records) == 1
    return records[0]


@pytest.mark.parametrize(
    ("provider_id", "model", "endpoint"),
    [
        ("minimax", "MiniMax-Hailuo-2.3", "minimax-hailuo-v1"),
        ("minimax", "MiniMax-Hailuo-2.3-Fast", "minimax-hailuo-v1-fast"),
        ("minimax", "S2V-01", "minimax-s2v-01"),
        ("minimax", "proxy/minimax-h3", "minimax-h3"),
    ],
)
def test_migrated_builtin_video_providers_use_shipped_definitions(provider_id: str, model: str, endpoint: str):
    backend = get_provider_spec(provider_id, "video").build_backend(
        _loaded(credentials={"api_key": "sk-test"}, provider_id=provider_id), model
    )

    assert isinstance(backend, DeclarativeVideoBackend)
    assert backend.model == model
    assert backend.name == provider_id
    assert backend._definition is ENDPOINT_REGISTRY[endpoint].definition


class TestBuildSimpleBaseUrlPriority:
    """简单族 base_url 优先级：用户显式 > registry default > 不传。"""

    def test_ark_image_falls_back_to_registry_default(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="ark")
        assert _built(get_provider_spec("ark", "image"), config, "doubao-seed-2-0-pro-260215") == {
            "media": "image",
            "backend": "ark",
            "kwargs": {
                "api_key": "sk-test",
                "model": "doubao-seed-2-0-pro-260215",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            },
        }

    def test_user_base_url_wins_over_registry_default(self):
        config = _loaded(
            credentials={"api_key": "sk-test", "base_url": "https://custom.example.com/v3"},
            provider_id="ark",
        )
        assert _built(get_provider_spec("ark", "image"), config, "model-x")["kwargs"] == {
            "api_key": "sk-test",
            "model": "model-x",
            "base_url": "https://custom.example.com/v3",
        }

    def test_ark_agent_plan_uses_own_plan_base_url(self):
        # ark-agent-plan 媒体侧复用 Ark backend，但 registry default 是独立的 /api/plan/v3
        # （非 ark 的 /api/v3）。
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="ark-agent-plan")
        assert _built(get_provider_spec("ark-agent-plan", "video"), config, "doubao-seedance-2.0") == {
            "media": "video",
            "backend": "ark-agent-plan",
            "kwargs": {
                "api_key": "sk-test",
                "model": "doubao-seedance-2.0",
                "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
            },
        }

    def test_grok_image_no_default_no_user_omits_base_url(self):
        # grok 无 registry default 且用户未配 → 不传 base_url（grok backend 不接受该参数）
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="grok")
        assert _built(get_provider_spec("grok", "image"), config, "grok-2-image")["kwargs"] == {
            "api_key": "sk-test",
            "model": "grok-2-image",
        }

    def test_missing_api_key_omitted_so_sdk_env_fallback_survives(self):
        # 用户未配 api_key → 不传 api_key（而非传 None）：让 backend 各自决定环境变量兜底
        # （OpenAI SDK 读 OPENAI_API_KEY）或 fail-loud；显式 None 会覆盖兜底。
        config = _loaded(credentials={}, provider_id="openai")
        assert _built(get_provider_spec("openai", "image"), config, "gpt-image-1")["kwargs"] == {"model": "gpt-image-1"}


class TestMediaRegistryRouting:
    """_build_simple 按 media_type 选对应 registry 的 create_backend（唯一分支逻辑）。"""

    def test_dashscope_video_uses_video_registry_and_default(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        assert _built(get_provider_spec("dashscope", "video"), config, "wan2.7-r2v") == {
            "media": "video",
            "backend": "dashscope",
            "kwargs": {
                "api_key": "sk-test",
                "model": "wan2.7-r2v",
                "base_url": "https://dashscope.aliyuncs.com",
            },
        }

    def test_dashscope_video_passes_wan3_base_url(self):
        # wan3 专用 maas 域名单列一键，仅 video lane 消费；未填时不写入 kwargs
        config = _loaded(
            credentials={"api_key": "sk-test", "wan3_base_url": "https://maas.example.com/ws-1/api/v1"},
            provider_id="dashscope",
        )
        built = _built(get_provider_spec("dashscope", "video"), config, "wan3.0-video")
        assert built["kwargs"]["wan3_base_url"] == "https://maas.example.com/ws-1/api/v1"

    def test_dashscope_video_omits_unset_wan3_base_url(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        built = _built(get_provider_spec("dashscope", "video"), config, "wan3.0-video")
        assert "wan3_base_url" not in built["kwargs"]

    def test_dashscope_audio_uses_audio_registry(self):
        config = _loaded(credentials={"api_key": "sk-test"}, provider_id="dashscope")
        assert _built(get_provider_spec("dashscope", "audio"), config, "qwen3-tts-flash") == {
            "media": "audio",
            "backend": "dashscope",
            "kwargs": {
                "api_key": "sk-test",
                "model": "qwen3-tts-flash",
                "base_url": "https://dashscope.aliyuncs.com",
            },
        }


class TestGeminiSpec:
    """gemini 特例族：backend_type 按 provider_id 分叉（aistudio/vertex 各一行），image/video 对等透传
    base_url，注入共享 rate_limiter，image_model/video_model 命名差异。api_key 与 base_url 无条件透传
    （含 None）：由 backend 内 resolve_gemini_api_key / normalize_base_url 处理 None（读环境变量 / 省略），
    vertex 分支结构性忽略 base_url（vertexai=True + 服务账号凭证，无注入点）。"""

    def test_aistudio_image_sets_base_url_and_image_model(self):
        spec = get_provider_spec("gemini-aistudio", "image")
        assert spec.registry_backend == "gemini"
        limiter = object()
        config = LoadedConfig(
            credentials={"api_key": "sk-aistudio", "base_url": "https://custom.example.com"},
            provider_meta=PROVIDER_REGISTRY.get("gemini-aistudio"),
            rate_limiter=limiter,
        )
        assert _built(spec, config, "gemini-3.1-flash-image-preview") == {
            "media": "image",
            "backend": "gemini",
            "kwargs": {
                "backend_type": "aistudio",
                "api_key": "sk-aistudio",
                "base_url": "https://custom.example.com",
                "rate_limiter": limiter,
                "image_model": "gemini-3.1-flash-image-preview",
            },
        }

    def test_vertex_image_backend_type_vertex(self):
        config = LoadedConfig(
            credentials={"api_key": None, "base_url": None},
            provider_meta=PROVIDER_REGISTRY.get("gemini-vertex"),
            rate_limiter=None,
        )
        # vertex 无 api_key/base_url：仍无条件透传 None（backend 内回落凭证文件 / 省略 base_url）
        assert _built(get_provider_spec("gemini-vertex", "image"), config, None)["kwargs"] == {
            "backend_type": "vertex",
            "api_key": None,
            "base_url": None,
            "rate_limiter": None,
            "image_model": None,
        }

    def test_aistudio_video_sets_base_url_uses_video_model(self):
        spec = get_provider_spec("gemini-aistudio", "video")
        assert spec.registry_backend == "gemini"
        limiter = object()
        config = LoadedConfig(
            credentials={"api_key": "sk-aistudio", "base_url": "https://custom.example.com"},
            provider_meta=PROVIDER_REGISTRY.get("gemini-aistudio"),
            rate_limiter=limiter,
        )
        # video 与 image 对等：aistudio 透传用户 base_url，命名参数是 video_model 不是 image_model
        assert _built(spec, config, "veo-3.1-lite-generate-preview") == {
            "media": "video",
            "backend": "gemini",
            "kwargs": {
                "backend_type": "aistudio",
                "api_key": "sk-aistudio",
                "base_url": "https://custom.example.com",
                "rate_limiter": limiter,
                "video_model": "veo-3.1-lite-generate-preview",
            },
        }

    def test_vertex_video_backend_type_vertex(self):
        config = LoadedConfig(
            credentials={"api_key": None, "base_url": None},
            provider_meta=PROVIDER_REGISTRY.get("gemini-vertex"),
            rate_limiter=None,
        )
        # vertex 无 api_key/base_url：仍无条件透传 None（与 vertex image 对称，backend 内结构性忽略）
        assert _built(get_provider_spec("gemini-vertex", "video"), config, "veo-3.1-generate-preview")["kwargs"] == {
            "backend_type": "vertex",
            "api_key": None,
            "base_url": None,
            "rate_limiter": None,
            "video_model": "veo-3.1-generate-preview",
        }

    def test_bare_gemini_not_registered(self):
        # 裸 "gemini"（无 aistudio/vertex 后缀）是死路径：resolver 只产出带后缀 id。
        # fail-loud，不为死路径登记兜底行。
        assert ("gemini", "image") not in PROVIDER_SPEC_REGISTRY
        assert ("gemini", "video") not in PROVIDER_SPEC_REGISTRY


class TestKlingSpec:
    """kling 特例族：双模式鉴权二选一（api_key 优先 → auth_mode=bearer；否则 access_key+secret_key
    → auth_mode=jwt），image 侧 api_model_name 解耦（两栖别名键读 registry api_model_name）、
    base_url 兜底（db > registry default，国内域名已迁移至 api-beijing）。
    video backend 不接受 api_model_name —— 非对称，video 闭包不传。"""

    @staticmethod
    def _kling_config(**credentials) -> LoadedConfig:
        return LoadedConfig(
            credentials=credentials,
            provider_meta=PROVIDER_REGISTRY.get("kling"),
            rate_limiter=None,
        )

    def test_image_dual_secret_and_jwt(self):
        spec = get_provider_spec("kling", "image")
        assert spec.registry_backend == "kling"
        config = self._kling_config(access_key="ak-1", secret_key="sk-1")
        assert _built(spec, config, "kling-image-o1") == {
            "media": "image",
            "backend": "kling",
            "kwargs": {
                "auth_mode": "jwt",
                "access_key": "ak-1",
                "secret_key": "sk-1",
                "model": "kling-image-o1",
                "base_url": "https://api-beijing.klingai.com/v1",
            },
        }

    def test_image_api_model_name_decoupled_for_amphibious_alias(self):
        # 两栖别名键 kling-v3-omni-image 的 registry api_model_name 是 kling-v3-omni（发真实 API 名）。
        config = self._kling_config(access_key="ak-1", secret_key="sk-1")
        assert _built(get_provider_spec("kling", "image"), config, "kling-v3-omni-image")["kwargs"] == {
            "auth_mode": "jwt",
            "access_key": "ak-1",
            "secret_key": "sk-1",
            "model": "kling-v3-omni-image",
            "api_model_name": "kling-v3-omni",
            "base_url": "https://api-beijing.klingai.com/v1",
        }

    def test_image_user_base_url_wins_over_registry_default(self):
        config = self._kling_config(access_key="ak-1", secret_key="sk-1", base_url="https://relay.example.com")
        assert _built(get_provider_spec("kling", "image"), config, "kling-image-o1")["kwargs"] == {
            "auth_mode": "jwt",
            "access_key": "ak-1",
            "secret_key": "sk-1",
            "model": "kling-image-o1",
            "base_url": "https://relay.example.com",
        }

    def test_video_dual_secret_no_api_model_name(self):
        spec = get_provider_spec("kling", "video")
        assert spec.registry_backend == "kling"
        config = self._kling_config(access_key="ak-1", secret_key="sk-1")
        # video backend 不接受 api_model_name：即使 model 是别名也不传该参数
        assert _built(spec, config, "kling-v3") == {
            "media": "video",
            "backend": "kling",
            "kwargs": {
                "auth_mode": "jwt",
                "access_key": "ak-1",
                "secret_key": "sk-1",
                "model": "kling-v3",
                "base_url": "https://api-beijing.klingai.com/v1",
            },
        }

    def test_video_api_key_dispatches_bearer_mode(self):
        """单填 api_key（无 access_key/secret_key）→ auth_mode=bearer，不透传 access_key/secret_key。"""
        config = self._kling_config(api_key="sk-api-1")
        assert _built(get_provider_spec("kling", "video"), config, "kling-v3")["kwargs"] == {
            "auth_mode": "bearer",
            "api_key": "sk-api-1",
            "model": "kling-v3",
            "base_url": "https://api-beijing.klingai.com/v1",
        }

    def test_image_api_key_dispatches_bearer_mode_with_api_model_name(self):
        """image 侧 bearer 模式同样叠加 api_model_name 解耦（两栖别名键）。"""
        config = self._kling_config(api_key="sk-api-1")
        assert _built(get_provider_spec("kling", "image"), config, "kling-v3-omni-image")["kwargs"] == {
            "auth_mode": "bearer",
            "api_key": "sk-api-1",
            "model": "kling-v3-omni-image",
            "api_model_name": "kling-v3-omni",
            "base_url": "https://api-beijing.klingai.com/v1",
        }

    def test_api_key_takes_priority_over_dual_secret_when_both_set(self):
        """两者都填时 api_key 优先（不透传 access_key/secret_key）。"""
        config = self._kling_config(api_key="sk-api-1", access_key="ak-1", secret_key="sk-1")
        assert _built(get_provider_spec("kling", "video"), config, "kling-v3")["kwargs"] == {
            "auth_mode": "bearer",
            "api_key": "sk-api-1",
            "model": "kling-v3",
            "base_url": "https://api-beijing.klingai.com/v1",
        }


class TestTextSimpleSpec:
    """简单文本族（ark / ark-agent-plan / grok / agnes）：model + api_key（无条件透传）+ base_url
    （user > registry default，仅非空才传）。映射到文本 registry 的 create_backend，registry_backend
    即 provider_id 自身。"""

    def test_ark_falls_back_to_registry_default(self):
        spec = get_provider_spec("ark", "text")
        assert spec.registry_backend == "ark"
        config = _loaded(credentials={"api_key": "ark-key"}, provider_id="ark")
        assert _built(spec, config, "doubao-seed-2-0-lite-260215") == {
            "media": "text",
            "backend": "ark",
            "kwargs": {
                "model": "doubao-seed-2-0-lite-260215",
                "api_key": "ark-key",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            },
        }

    def test_ark_agent_plan_uses_plan_base_url(self):
        spec = get_provider_spec("ark-agent-plan", "text")
        assert spec.registry_backend == "ark-agent-plan"
        config = _loaded(credentials={"api_key": "k"}, provider_id="ark-agent-plan")
        assert _built(spec, config, "doubao-seed-2.0-lite")["kwargs"] == {
            "model": "doubao-seed-2.0-lite",
            "api_key": "k",
            "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        }

    def test_user_base_url_wins(self):
        config = _loaded(credentials={"api_key": "k", "base_url": "https://relay.test/v3"}, provider_id="ark")
        built = _built(get_provider_spec("ark", "text"), config, "m")
        assert built["kwargs"]["base_url"] == "https://relay.test/v3"

    def test_grok_no_default_no_user_omits_base_url(self):
        config = _loaded(credentials={"api_key": "grok-key"}, provider_id="grok")
        assert _built(get_provider_spec("grok", "text"), config, "grok-4")["kwargs"] == {
            "model": "grok-4",
            "api_key": "grok-key",
        }

    def test_agnes_falls_back_to_registry_default_base_url(self):
        # agnes 走简单文本族（registry_backend = provider_id），无用户 base_url 时回落 registry default。
        spec = get_provider_spec("agnes", "text")
        assert spec.registry_backend == "agnes"
        config = _loaded(credentials={"api_key": "ag-key"}, provider_id="agnes")
        assert _built(spec, config, "agnes-2.0-flash")["kwargs"] == {
            "model": "agnes-2.0-flash",
            "api_key": "ag-key",
            "base_url": "https://apihub.agnes-ai.com/v1",
        }

    def test_api_key_passed_unconditionally_even_when_missing(self):
        # 文本简单族 api_key 无条件透传（含 None），与媒体简单族「缺省省略」非对称。
        config = _loaded(credentials={}, provider_id="grok")
        assert _built(get_provider_spec("grok", "text"), config, "grok-4")["kwargs"] == {
            "model": "grok-4",
            "api_key": None,
        }


class TestTextGeminiSpec:
    """gemini 文本：aistudio（base_url 无条件透传用户值）/ vertex（backend=vertex + gcs_bucket）
    按 provider_id 分两行，registry_backend 同为 "gemini"。文本 gemini 不接受 rate_limiter。"""

    def test_aistudio_passes_user_base_url_unconditionally(self):
        spec = get_provider_spec("gemini-aistudio", "text")
        assert spec.registry_backend == "gemini"
        config = _loaded(credentials={"api_key": "g-key", "base_url": ""}, provider_id="gemini-aistudio")
        # base_url 无条件透传（含空串），不回落 registry default
        assert _built(spec, config, "gemini-3-flash-preview") == {
            "media": "text",
            "backend": "gemini",
            "kwargs": {"model": "gemini-3-flash-preview", "api_key": "g-key", "base_url": ""},
        }

    def test_vertex_uses_gcs_bucket_no_api_key(self):
        spec = get_provider_spec("gemini-vertex", "text")
        assert spec.registry_backend == "gemini"
        config = _loaded(credentials={"gcs_bucket": "my-bucket"}, provider_id="gemini-vertex")
        assert _built(spec, config, "gemini-3-flash-preview")["kwargs"] == {
            "model": "gemini-3-flash-preview",
            "backend": "vertex",
            "gcs_bucket": "my-bucket",
        }


class TestTextOpenAICompatSpec:
    """OpenAI-compat 文本（openai / dashscope / minimax）都映射到 "openai" registry backend。
    openai 直传用户 base_url；dashscope/minimax 经 helper 从 host 派生 base_url 并透传 provider_name
    计费归因（保证 usage 记账命中自身 CNY 费率，非 OpenAI USD）。"""

    def test_openai_passes_user_base_url_no_provider_name(self):
        spec = get_provider_spec("openai", "text")
        assert spec.registry_backend == "openai"
        config = _loaded(credentials={"api_key": "oa", "base_url": "https://relay.test/v1"}, provider_id="openai")
        assert _built(spec, config, "gpt-5") == {
            "media": "text",
            "backend": "openai",
            "kwargs": {"model": "gpt-5", "api_key": "oa", "base_url": "https://relay.test/v1"},
        }

    def test_dashscope_derives_base_url_and_passes_provider_name(self):
        spec = get_provider_spec("dashscope", "text")
        assert spec.registry_backend == "openai"
        config = _loaded(credentials={"api_key": "ds"}, provider_id="dashscope")
        assert _built(spec, config, "qwen-max")["kwargs"] == {
            "model": "qwen-max",
            "api_key": "ds",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "provider_name": "dashscope",
        }

    def test_dashscope_user_host_derives_compatible_mode_path(self):
        # 用户填自定义 host → helper 仍派生 /compatible-mode/v1 后缀
        config = _loaded(
            credentials={"api_key": "ds", "base_url": "https://dashscope-intl.aliyuncs.com"},
            provider_id="dashscope",
        )
        built = _built(get_provider_spec("dashscope", "text"), config, "qwen-max")
        assert built["kwargs"]["base_url"] == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    def test_minimax_derives_base_url_and_passes_provider_name(self):
        spec = get_provider_spec("minimax", "text")
        assert spec.registry_backend == "openai"
        config = _loaded(credentials={"api_key": "mm"}, provider_id="minimax")
        assert _built(spec, config, "minimax-text-01")["kwargs"] == {
            "model": "minimax-text-01",
            "api_key": "mm",
            "base_url": "https://api.minimaxi.com/v1",
            "provider_name": "minimax",
        }


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

    def test_text_family_complete(self):
        # 文本九对：七 provider + gemini 两 id（aistudio/vertex）
        text_keys = {k for k in PROVIDER_SPEC_REGISTRY if k[1] == "text"}
        assert text_keys == {
            ("ark", "text"),
            ("ark-agent-plan", "text"),
            ("grok", "text"),
            ("agnes", "text"),
            ("gemini-aistudio", "text"),
            ("gemini-vertex", "text"),
            ("openai", "text"),
            ("dashscope", "text"),
            ("minimax", "text"),
        }

    def test_bare_gemini_text_not_registered(self):
        # 与媒体侧一致：裸 "gemini" 是死路径，resolver 只产出带后缀 id，fail-loud 不登记兜底行
        assert ("gemini", "text") not in PROVIDER_SPEC_REGISTRY


class TestValidateProviderSpecs:
    """import 期不变式：build 可调用、键与 spec 字段一致、media_type 合法。misconfig fail-fast。"""

    def test_passes_on_real_registry(self):
        _validate_provider_specs()  # 真表不抛
        assert PROVIDER_SPEC_REGISTRY  # 空表也不抛，校验须建立在真表非空之上

    def test_non_callable_build_rejected(self, monkeypatch: pytest.MonkeyPatch):
        bad = dataclasses.replace(PROVIDER_SPEC_REGISTRY[("ark", "image")], build_backend="not-callable")
        monkeypatch.setitem(PROVIDER_SPEC_REGISTRY, ("ark", "image"), bad)
        with pytest.raises(ValueError, match="non-callable build_backend"):
            _validate_provider_specs()

    def test_key_field_mismatch_rejected(self, monkeypatch: pytest.MonkeyPatch):
        # spec 内 provider_id/media_type 与字典键漂移 → fail-fast
        bad = dataclasses.replace(PROVIDER_SPEC_REGISTRY[("ark", "image")], provider_id="drifted")
        monkeypatch.setitem(PROVIDER_SPEC_REGISTRY, ("ark", "image"), bad)
        with pytest.raises(ValueError, match=r"key .* does not match spec"):
            _validate_provider_specs()

    def test_registry_backend_names_are_registered(self):
        """registry 名都在对应后端 registry 里 —— 归单测（import 全部后端无碍），不进 import 期。"""
        from lib.audio_backends import get_registered_backends as audio_names
        from lib.image_backends import get_registered_backends as image_names
        from lib.text_backends import get_registered_backends as text_names
        from lib.video_backends import get_registered_backends as video_names

        registered = {
            "image": set(image_names()),
            "video": set(video_names()),
            "audio": set(audio_names()),
            "text": set(text_names()),
        }
        for (_provider, media), spec in PROVIDER_SPEC_REGISTRY.items():
            if spec.registry_backend == "declarative":
                continue
            assert spec.registry_backend in registered[media], (
                f"{spec.registry_backend!r} 未注册到 {media} backend registry"
            )


class TestBuiltinEffectiveGenerateAudio:
    """声明式内置模型的计价 ``generate_audio`` 与定义声明的成片音轨同源。"""

    def test_an_always_off_declarative_model_reports_no_audio(self):
        from lib.backend_assembly.specs import builtin_effective_generate_audio_for_model

        assert builtin_effective_generate_audio_for_model("minimax", "MiniMax-Hailuo-2.3") is False

    def test_an_always_on_declarative_model_reports_audio(self):
        from lib.backend_assembly.specs import builtin_effective_generate_audio_for_model

        assert builtin_effective_generate_audio_for_model("minimax", "MiniMax-H3") is True
