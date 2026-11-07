"""Reference-to-video exceptions."""

from __future__ import annotations


class ProviderUnsupportedFeatureError(Exception):
    """Provider does not support a capability (such as multiple Sora references)."""

    def __init__(self, *, provider: str, feature: str):
        self.provider = provider
        self.feature = feature
        super().__init__(f"Provider {provider} does not support {feature}")
