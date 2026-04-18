"""参考生视频模式专用异常。"""

from __future__ import annotations


class MissingReferenceError(Exception):
    """@ 提及解析到不存在或无 sheet 的资源。"""

    def __init__(self, *, missing: list[tuple[str, str]]):
        if not missing:
            raise ValueError("missing must be non-empty")
        self.missing = missing
        names = ", ".join(f"{t}:{n}" for t, n in missing)
        super().__init__(f"Missing references: {names}")


class RequestPayloadTooLargeError(Exception):
    """视频生成请求体超出供应商限制（gRPC/HTTP body size）。"""

    def __init__(self, message: str = "Request payload too large"):
        super().__init__(message)


class ProviderUnsupportedFeatureError(Exception):
    """供应商不支持某项能力（如 Sora 多参考图）。"""

    def __init__(self, *, provider: str, feature: str):
        self.provider = provider
        self.feature = feature
        super().__init__(f"Provider {provider} does not support {feature}")
