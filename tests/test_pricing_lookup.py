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
        # 回落到 ark 默认视频模型
        assert "doubao-seedance-1-5-pro-251215" in pricing.rates
        assert any("no-such-model" in r.message for r in caplog.records)

    def test_agent_plan_no_pricing_falls_back_to_gemini_quietly(self, caplog):
        with caplog.at_level(logging.WARNING, logger="lib.pricing.lookup"):
            pricing = lookup_pricing("ark-agent-plan", "doubao-seedance-2.0", "video")
        # Agent Plan 模型 pricing=None → 回落 Gemini 默认视频费率，不发 WARNING
        assert isinstance(pricing, PerSecondMatrix)
        assert "veo-3.1-lite-generate-preview" in pricing.rates
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)


class TestHiddenModelStillResolves:
    def test_hidden_does_not_block_lookup(self, monkeypatch):
        # 成本快照边角：模型被下线（hidden=True）后，入队遗留任务仍需算价。
        meta = PROVIDER_REGISTRY["gemini-aistudio"]
        base = meta.models["gemini-3-flash-preview"]
        hidden_model = replace(base, hidden=True)
        monkeypatch.setitem(meta.models, "gemini-3-flash-retired", hidden_model)
        pricing = lookup_pricing("gemini-aistudio", "gemini-3-flash-retired", "text")
        assert pricing is hidden_model.pricing
