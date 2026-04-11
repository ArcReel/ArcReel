"""
Grok (xAI) 共享工具模块

供 text_backends / image_backends / video_backends 复用。

包含：
- create_grok_client — xAI AsyncClient 客户端工厂
- grok_should_retry — gRPC 感知的重试谓词
"""

from __future__ import annotations

from lib.retry import BASE_RETRYABLE_ERRORS, _should_retry

# gRPC 瞬态状态码（等价于 HTTP 429/500/502/503/504）
_GRPC_RETRYABLE_CODES: set | None = None

try:
    import grpc

    _GRPC_RETRYABLE_CODES = {
        grpc.StatusCode.UNAVAILABLE,  # 503 — 连接中断 / Socket closed
        grpc.StatusCode.DEADLINE_EXCEEDED,  # 504 — 超时
        grpc.StatusCode.RESOURCE_EXHAUSTED,  # 429 — 限流
        grpc.StatusCode.ABORTED,  # 冲突重试
    }
except ImportError:
    pass


def grok_should_retry(exc: Exception) -> bool:
    """Grok 专用重试谓词：精确匹配 gRPC 瞬态状态码，其余回退默认模式匹配。"""
    if _GRPC_RETRYABLE_CODES is not None:
        try:
            from grpc.aio import AioRpcError

            if isinstance(exc, AioRpcError):
                return exc.code() in _GRPC_RETRYABLE_CODES
        except ImportError:
            pass
    return _should_retry(exc, BASE_RETRYABLE_ERRORS)


def create_grok_client(*, api_key: str | None = None):
    """创建 xAI AsyncClient，统一校验和构造。"""
    import xai_sdk

    if not api_key:
        raise ValueError("XAI_API_KEY 未设置\n请在系统配置页中配置 xAI API Key")
    return xai_sdk.AsyncClient(api_key=api_key)
