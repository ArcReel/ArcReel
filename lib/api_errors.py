"""携带 (status_code, i18n key, params) 的领域异常。

约定：
- lib / service 层抛出时只带 i18n key 与 params，不带成品文案；
- server 注册的 app 级 exception handler（``server/error_handlers.py``）单点完成
  状态码映射、按请求 Accept-Language 翻译与脱敏，路由函数体只保留 happy path；
- ``str(exc)`` 只进服务端日志，永不面向客户端输出。
"""

from __future__ import annotations


class ApiError(Exception):
    """领域异常基类：由 app 级 exception handler 统一翻译为 ``{"detail": ...}`` 响应。"""

    def __init__(self, key: str, *, status_code: int, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.status_code = status_code
        self.params = params


class BadRequestError(ApiError):
    """客户端请求错误（HTTP 400）。"""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key, status_code=400, **params)


class NotFoundError(ApiError):
    """请求的资源不存在（HTTP 404）。"""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key, status_code=404, **params)
