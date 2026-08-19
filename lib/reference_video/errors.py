"""参考生视频模式专用异常。"""

from __future__ import annotations


class ProviderUnsupportedFeatureError(Exception):
    """供应商不支持某项能力（如 Sora 多参考图）。"""

    def __init__(self, *, provider: str, feature: str):
        self.provider = provider
        self.feature = feature
        super().__init__(f"Provider {provider} does not support {feature}")
