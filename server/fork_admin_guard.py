"""fork-private: 路径级 admin guard 中间件。

设计目的：把"settings 类路由仅 admin 可访问"的约束实现在 fork 私有文件里，
**不修改上游 router 文件**，从而把合并冲突面压到 0。

工作方式：
- 维护一份正则白名单 ``ADMIN_PATH_PATTERNS``，列出当前需要 admin 才能访问的路径。
- 注册一个 HTTP middleware：请求路径匹配任一正则时，解析 token（JWT/API Key
  二者皆走 :func:`server.auth._verify_and_get_payload_async`），从 payload 读取
  role；非 admin 直接 403。
- 普通业务 router 不受影响（路径不在白名单里）。

漂移风险与对策：
- 上游若改路径或删 router，正则会失配 → :mod:`tests.test_fork_admin_guard` 会
  失败，从而显式提醒维护者更新本文件。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

# ---------------------------------------------------------------------------
# 路径正则白名单
# ---------------------------------------------------------------------------
#
# 注意：每条正则末尾用 ``(?:/|$)`` 避免误匹配类似 ``/api/v1/users-export`` 的相邻路径。
ADMIN_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/api/v1/providers(?:/|$)"),
    re.compile(r"^/api/v1/custom-providers(?:/|$)"),
    re.compile(r"^/api/v1/system/(?:config|version)(?:/|$)"),
    re.compile(r"^/api/v1/api-keys(?:/|$)"),
    re.compile(r"^/api/v1/users(?:/|$)"),
    # /api/v1/agent/credentials* + /preset-providers + /test-connection 锁 admin；
    # /api/v1/agent/chat 是外部 Agent (OpenClaw 等) 对话端点，不在此白名单内。
    re.compile(r"^/api/v1/agent/(?:credentials|preset-providers|test-connection)(?:/|$)"),
)

# 普通用户（非 admin）允许的只读组合：路径正则 + HTTP 方法集合。
# 例：项目设置页需要 GET /api/v1/system/config 拿默认模型；创建项目对话框 / 配置状态襳需要 providers 列表。
# 写仍需 admin。
READ_ALLOWLIST: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"^/api/v1/system/config$"), frozenset({"GET"})),
    (re.compile(r"^/api/v1/providers(?:/[^/]+)?$"), frozenset({"GET"})),
    (re.compile(r"^/api/v1/custom-providers(?:/[^/]+)?$"), frozenset({"GET"})),
    (re.compile(r"^/api/v1/custom-providers/endpoints$"), frozenset({"GET"})),
)


def _is_read_allowed(path: str, method: str) -> bool:
    return any(p.match(path) and method.upper() in m for p, m in READ_ALLOWLIST)


def _is_admin_path(path: str) -> bool:
    return any(p.match(path) for p in ADMIN_PATH_PATTERNS)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    # 兼容 EventSource 等不能自定义 header 的场景：?token= 兜底
    token = request.query_params.get("token")
    return token or None


def _resolve_role(payload: dict) -> str:
    # API Key 路径在 fork 中固定视为非 admin
    if payload.get("via") == "apikey":
        return "user"
    # 不接受缺少 role claim 的旧 token；返回空串会被上层 != "admin" 判为 403
    return payload.get("role") or ""


def install_admin_guard(app: FastAPI) -> None:
    """挂载 fork-private admin guard 中间件到 ``app``。

    幂等：重复调用只会注册一次。
    """
    if getattr(app.state, "_fork_admin_guard_installed", False):
        return
    app.state._fork_admin_guard_installed = True

    from server.auth import _verify_and_get_payload_async  # 局部导入避免循环依赖

    @app.middleware("http")
    async def _admin_guard(request: Request, call_next):  # type: ignore[no-redef]
        if not _is_admin_path(request.url.path):
            return await call_next(request)

        token = _extract_token(request)
        if not token:
            return JSONResponse(
                {"detail": "未认证"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            payload = await _verify_and_get_payload_async(token)
        except Exception as exc:  # HTTPException or 其他
            status = getattr(exc, "status_code", 401)
            detail = getattr(exc, "detail", "token 无效或已过期")
            return JSONResponse({"detail": detail}, status_code=status)

        if _resolve_role(payload) != "admin":
            if _is_read_allowed(request.url.path, request.method):
                return await call_next(request)
            return JSONResponse({"detail": "需要管理员权限"}, status_code=403)
        return await call_next(request)


def iter_admin_path_patterns() -> Iterable[re.Pattern[str]]:
    """供测试枚举正则。"""
    return ADMIN_PATH_PATTERNS
