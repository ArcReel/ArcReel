"""``lookup_pricing`` 的回落路径：registry 命中 / 未知 provider / Anthropic 例外 / vidu 委托 /
未知 model 回落默认 + 告警 / Agent Plan 无定价回落 Gemini / hidden 模型仍可命中。"""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from lib.config.registry import PROVIDER_REGISTRY
from lib.pricing.lookup import lookup_pricing
from lib.pricing.types import (
    PerImageByResolution,
    PerImageFlat,
    PerImageOpenAIToken,
    PerSecondMatrix,
    PerToken,
    PerTokenVideo,
    ViduDelegate,
)


class TestRegistryHit:
    def test_gemini_image_flash(self):
        pricing = lookup_pricing("gemini-aistudio", "gemini-3.1-flash-image-preview", "image")
        assert isinstance(pricing, PerImageByResolution)
        assert pricing.rates["gemini-3.1-flash-image-preview"]["1K"] == 0.067

    def test_vertex_video_001(self):
        pricing = lookup_pricing("gemini-vertex", "veo-3.1-fast-generate-001", "video")
        assert isinstance(pricing, PerSecondMatrix)
        assert pricing.dimensions == "resolution_audio"

    def test_openai_image_token(self):
        pricing = lookup_pricing("openai", "gpt-image-2", "image")
        assert isinstance(pricing, PerImageOpenAIToken)

    def test_ark_video_per_token(self):
        pricing = lookup_pricing("ark", "doubao-seedance-1-5-pro-251215", "video")
        assert isinstance(pricing, PerTokenVideo)
        assert pricing.currency == "CNY"

    def test_ark_seedance_2_mini_per_token(self):
        pricing = lookup_pricing("ark", "doubao-seedance-2-0-mini-260615", "video")
        assert isinstance(pricing, PerTokenVideo)
        assert pricing.currency == "CNY"

    def test_agnes_text_per_token(self):
        pricing = lookup_pricing("agnes", "agnes-2.0-flash", "text")
        assert isinstance(pricing, PerToken)
        assert pricing.currency == "USD"
        # 输入 $0.03 / 输出 $0.15 每 1M tokens
        assert pricing.rates["agnes-2.0-flash"] == {"input": 0.03, "output": 0.15}

    def test_agnes_video_flat_per_second(self):
        from lib.pricing.strategies import PricingParams, calculate_pricing

        pricing = lookup_pricing("agnes", "agnes-video-v2.0", "video")
        assert isinstance(pricing, PerSecondMatrix)
        assert pricing.dimensions == "flat"
        assert pricing.currency == "USD"
        # flat 按秒 $0.005/s，与分辨率/音频无关
        amount, currency = calculate_pricing(
            pricing, PricingParams(call_type="video", model="agnes-video-v2.0", duration_seconds=10)
        )
        assert amount == pytest.approx(0.05)
        assert currency == "USD"


class TestUnknownProviderFallsBackToGemini:
    @pytest.mark.parametrize("provider", ["gemini", "unknown", "seedance"])
    def test_text(self, provider: str):
        pricing = lookup_pricing(provider, None, "text")
        assert isinstance(pricing, PerToken)
        assert pricing.rates["gemini-3-flash-preview"] == {"input": 0.50, "output": 3.00}

    def test_image(self):
        pricing = lookup_pricing("unknown", None, "image")
        assert isinstance(pricing, PerImageByResolution)
        assert "gemini-3.1-flash-image-preview" in pricing.rates

    def test_video(self):
        pricing = lookup_pricing("unknown", None, "video")
        assert isinstance(pricing, PerSecondMatrix)
        assert "veo-3.1-lite-generate-preview" in pricing.rates


class TestAnthropicException:
    def test_anthropic_returns_per_token(self):
        pricing = lookup_pricing("anthropic", "claude-sonnet-4", "text")
        assert isinstance(pricing, PerToken)
        assert pricing.currency == "USD"
        assert pricing.rates["claude-sonnet-4"] == {"input": 3.00, "output": 15.00}

    def test_anthropic_unknown_model_still_anthropic_table(self):
        # Anthropic 走 provider 级例外，未知 model 不回落 Gemini，仍用 Anthropic 默认。
        pricing = lookup_pricing("anthropic", "unknown-claude", "text")
        assert isinstance(pricing, PerToken)
        assert pricing.default_model == "claude-sonnet-4"


class TestViduDelegate:
    def test_vidu_returns_delegate_provider_level(self):
        # 任意 model（含未知）都返回委托标记，不经 model→pricing 回落。
        assert isinstance(lookup_pricing("vidu", "viduq3-turbo", "video"), ViduDelegate)
        assert isinstance(lookup_pricing("vidu", "totally-unknown", "image"), ViduDelegate)


class TestUnknownModelFallback:
    def test_unknown_model_falls_back_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="lib.pricing.lookup"):
            pricing = lookup_pricing("ark", "no-such-model", "video")
        assert isinstance(pricing, PerTokenVideo)
        # 回落到 ark 默认视频模型（mini）
        assert "doubao-seedance-2-0-mini-260615" in pricing.rates
        assert any("no-such-model" in r.getMessage() for r in caplog.records)

    def test_agnes_unknown_image_model_falls_back_to_own_default(self, caplog):
        # agnes 有专属 PerImageFlat 表，未知 model 应回落 agnes 自有默认（$0.003 USD），
        # 而非 Gemini 通用图像费率——故 agnes 须在 _OWN_TABLE_PROVIDERS 内。
        with caplog.at_level(logging.WARNING, logger="lib.pricing.lookup"):
            pricing = lookup_pricing("agnes", "agnes-image-2.0-unregistered", "image")
        assert isinstance(pricing, PerImageFlat)
        assert pricing.rates["agnes-image-2.1-flash"] == 0.003
        assert pricing.currency == "USD"
        assert any("agnes-image-2.0-unregistered" in r.getMessage() for r in caplog.records)

    def test_agnes_unknown_text_model_falls_back_to_own_default(self, caplog):
        # agnes 有专属 PerToken 文本表，未知 model 应回落 agnes 自有默认文本费率（非 Gemini 通用），
        # 故 agnes 须在 _OWN_TABLE_PROVIDERS 内。
        with caplog.at_level(logging.WARNING, logger="lib.pricing.lookup"):
            pricing = lookup_pricing("agnes", "agnes-2.0-unregistered", "text")
        assert isinstance(pricing, PerToken)
        assert pricing.rates["agnes-2.0-flash"] == {"input": 0.03, "output": 0.15}
        assert pricing.currency == "USD"
        assert any("agnes-2.0-unregistered" in r.getMessage() for r in caplog.records)

    def test_agent_plan_no_pricing_falls_back_to_gemini_quietly(self, caplog):
        with caplog.at_level(logging.WARNING, logger="lib.pricing.lookup"):
            pricing = lookup_pricing("ark-agent-plan", "doubao-seedance-2.0", "video")
        # Agent Plan 模型 pricing=None → 回落 Gemini 默认视频费率，不发 WARNING
        assert isinstance(pricing, PerSecondMatrix)
        assert "veo-3.1-lite-generate-preview" in pricing.rates
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)


class TestGeminiFamilyVideoFallback:
    """gemini-aistudio/vertex 的未知/None 视频模型应回落到 Gemini 通用默认（lite），
    而非各 provider 自身默认（vertex 视频默认是 fast-001），保持历史费率一致。"""

    def test_vertex_none_model_uses_universal_lite_not_vertex_fast_default(self):
        pricing = lookup_pricing("gemini-vertex", None, "video")
        assert isinstance(pricing, PerSecondMatrix)
        # 通用默认 = veo-3.1-lite-generate-preview（1080p+audio=0.08），不是 vertex 默认 fast-001（0.15）
        assert "veo-3.1-lite-generate-preview" in pricing.rates
        assert pricing.rates["veo-3.1-lite-generate-preview"][("1080p", True)] == 0.08

    def test_vertex_unknown_model_uses_universal_lite(self):
        pricing = lookup_pricing("gemini-vertex", "veo-removed-from-registry", "video")
        assert isinstance(pricing, PerSecondMatrix)
        assert "veo-3.1-lite-generate-preview" in pricing.rates

    def test_vertex_known_model_still_resolves_own_pricing(self):
        # 正常配对（vertex + 其自有模型）仍命中该模型自身费率。
        pricing = lookup_pricing("gemini-vertex", "veo-3.1-generate-001", "video")
        assert isinstance(pricing, PerSecondMatrix)
        assert pricing.rates["veo-3.1-generate-001"][("1080p", True)] == 0.40


class TestCrossModalModelMismatch:
    """模型名与 call_type 媒体类型不符时，应按当前媒体类型默认模型计价，而非该模型自身的异模态费率
    （复刻历史「按 call_type 分表」：模型名只在本模态费率表内查）。"""

    def test_openai_image_model_on_video_call_falls_back_to_video_default(self):
        # video 调用带图像模型名 → 不返回图片定价，回落 openai 视频默认（Sora，PerSecondMatrix）。
        pricing = lookup_pricing("openai", "gpt-image-2", "video")
        assert isinstance(pricing, PerSecondMatrix)

    def test_gemini_image_model_on_video_call_falls_back_to_video_default(self):
        # 非 OWN_TABLE 的 gemini 家族同理 → 回落 Gemini 视频通用默认（lite）。
        pricing = lookup_pricing("gemini-aistudio", "gemini-3.1-flash-image-preview", "video")
        assert isinstance(pricing, PerSecondMatrix)
        assert "veo-3.1-lite-generate-preview" in pricing.rates


class TestHiddenModelStillResolves:
    def test_hidden_does_not_block_lookup(self, monkeypatch):
        # 成本快照边角：模型被下线（hidden=True）后，入队遗留任务仍需算价。
        meta = PROVIDER_REGISTRY["gemini-aistudio"]
        base = meta.models["gemini-3-flash-preview"]
        hidden_model = replace(base, hidden=True)
        monkeypatch.setitem(meta.models, "gemini-3-flash-retired", hidden_model)
        pricing = lookup_pricing("gemini-aistudio", "gemini-3-flash-retired", "text")
        assert pricing is hidden_model.pricing
