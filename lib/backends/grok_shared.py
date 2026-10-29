"""
Grok (xAI) 共享工具模块

供 text_backends / image_backends / video_backends 复用。

包含：
- create_grok_client — xAI AsyncClient 客户端工厂（惰性创建，见 LazyGrokClient）
- grok_should_retry — gRPC 感知的重试谓词
- grpc_rejection — 把 gRPC 确定性拒绝换算成 HTTP 状态码与拒因摘要
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.infra.retry import BASE_RETRYABLE_ERRORS, should_retry

# gRPC 瞬态状态码（等价于 HTTP 429/500/502/503/504）
_GRPC_RETRYABLE_CODES: set | None = None
# gRPC 确定性拒绝 → 等价 HTTP 状态码（按 gRPC 官方 HTTP 映射）。改输入或凭证前重试无益。
_GRPC_REJECTION_STATUS: dict | None = None

try:
    import grpc

    _GRPC_RETRYABLE_CODES = {
        grpc.StatusCode.UNAVAILABLE,  # 503 — 连接中断 / Socket closed
        grpc.StatusCode.DEADLINE_EXCEEDED,  # 504 — 超时
        grpc.StatusCode.RESOURCE_EXHAUSTED,  # 429 — 限流
        grpc.StatusCode.ABORTED,  # 冲突重试
    }
    _GRPC_REJECTION_STATUS = {
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.FAILED_PRECONDITION: 400,
        grpc.StatusCode.OUT_OF_RANGE: 400,
        grpc.StatusCode.UNAUTHENTICATED: 401,
        grpc.StatusCode.PERMISSION_DENIED: 403,
        grpc.StatusCode.NOT_FOUND: 404,
    }
except ImportError:
    pass  # grpc is optional; fall back to pattern-based retry below

if TYPE_CHECKING:
    import xai_sdk
    from xai_sdk.aio import chat, image, video


def grok_should_retry(exc: Exception) -> bool:
    """Grok 专用重试谓词：精确匹配 gRPC 瞬态状态码，其余回退默认模式匹配。"""
    if _GRPC_RETRYABLE_CODES is not None:
        try:
            from grpc.aio import AioRpcError

            if isinstance(exc, AioRpcError):
                return exc.code() in _GRPC_RETRYABLE_CODES
        except ImportError:
            pass  # grpc available at module level but aio submodule missing; fall through
    return should_retry(exc, BASE_RETRYABLE_ERRORS)


def grpc_rejection(exc: BaseException) -> tuple[int, str | None] | None:
    """gRPC 确定性拒绝的等价 HTTP 状态码与脱敏拒因摘要；不是这类拒绝时为 ``None``。

    ``AioRpcError`` 的 ``str()`` 是整段调试表示（``<AioRpcError of RPC that terminated with …>``），
    直接落进任务失败原因用户读不懂。换算后落库侧按 ``provider_rejected`` 结构化编码。
    认证失败的详情讲的就是凭证本身，与 HTTP 401 同口径不透传。
    """
    if _GRPC_REJECTION_STATUS is None:
        return None
    try:
        from grpc.aio import AioRpcError
    except ImportError:
        return None
    if not isinstance(exc, AioRpcError):
        return None
    status = _GRPC_REJECTION_STATUS.get(exc.code())
    if status is None:
        return None
    if status == 401:
        return status, None
    from lib.backends.backend_runtime import summarize_provider_reason

    return status, summarize_provider_reason(exc.details())


def resolve_grok_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 xAI API Key")
    return api_key.strip()


class LazyGrokClient:
    """xAI AsyncClient 的惰性包装：首次访问 chat / image / video 时才创建 SDK 客户端。

    ``xai_sdk.AsyncClient`` 构造时就创建 grpc.aio 通道，要求当前线程有事件循环；而内置 backend 在
    ``asyncio.to_thread`` 的工作线程里构造（见 ``lib.backends.backend_assembly.assembler``）。backend 只在
    async 生成方法里访问这三个子客户端，此时位于事件循环线程，通道也随之绑定到实际使用它的事件循环。
    API Key 在构造时校验，缺失仍立即报错。
    """

    def __init__(self, *, api_key: str | None) -> None:
        self._api_key = resolve_grok_api_key(api_key)
        self._sdk_client: xai_sdk.AsyncClient | None = None

    def _sdk(self) -> xai_sdk.AsyncClient:
        if self._sdk_client is None:
            import xai_sdk

            self._sdk_client = xai_sdk.AsyncClient(api_key=self._api_key)
        return self._sdk_client

    @property
    def chat(self) -> chat.Client:
        return self._sdk().chat

    @property
    def image(self) -> image.Client:
        return self._sdk().image

    @property
    def video(self) -> video.Client:
        return self._sdk().video


def create_grok_client(*, api_key: str | None = None) -> LazyGrokClient:
    """创建 xAI 客户端，统一校验和构造；SDK 客户端推迟到首次使用时创建，backend 可在任意线程构造。"""
    return LazyGrokClient(api_key=api_key)
