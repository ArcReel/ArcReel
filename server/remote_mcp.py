"""Streamable-HTTP adapter for ArcReel's host-independent tools."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi.responses import PlainTextResponse
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from lib.project.project_manager import ProjectManager, get_project_manager
from lib.script.source_loader import SourceLoader
from server.agent_toolset.remote import remote_tools
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.auth import API_KEY_PREFIX, _verify_api_key
from server.tool_runtime import Services

_UNPUBLISHED_ISSUER_URL = AnyHttpUrl("http://localhost/")

# One decoded control byte may occupy six JSON bytes (``\u00XX``); leave 1 MiB for the MCP envelope.
_MAX_REQUEST_BODY_BYTES = SourceLoader.DEFAULT_MAX_BYTES * 6 + 1024 * 1024


class ArcApiKeyVerifier(TokenVerifier):
    """Bridge MCP Bearer auth to ArcReel's existing API Key verifier."""

    def __init__(self, verify_api_key: Callable[[str], Awaitable[dict[str, Any] | None]] = _verify_api_key) -> None:
        self._verify_api_key = verify_api_key

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(API_KEY_PREFIX):
            return None
        payload = await self._verify_api_key(token)
        if payload is None:
            return None
        return AccessToken(token=token, client_id=payload["sub"], scopes=["arcreel"])


def build_remote_mcp_server(
    *,
    projects: ProjectManager | None = None,
    services: Services | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastMCP:
    """Build one restart-safe MCP server instance for the host lifespan."""
    if services is not None:
        if projects is not None and projects.data_root.resolve() != services.projects.data_root.resolve():
            raise ValueError("projects 与 services.projects 必须属于同一项目根")
        projects = services.projects
    else:
        projects = projects or get_project_manager()
        services = Services.defaults(projects)

    return FastMCP(
        "arcreel",
        tools=remote_tools(AGENT_TOOLSET, projects=projects, services=services),
        token_verifier=token_verifier or ArcApiKeyVerifier(),
        # ArcReel 只认静态 arc- API Key，没有 OAuth 授权服务器（ADR 0065），不声明 RFC 9728
        # 受保护资源元数据：MCP 规范要求元数据至少列出一个授权服务器，声明了只会把发现型客户端
        # 引进注定失败的 OAuth 流程。不设 resource_server_url 时 401 只回普通 Bearer challenge；
        # issuer_url 是 SDK 必填字段，仅在注册授权路由或声明元数据时才会对外出现，这里两者都不发生。
        auth=AuthSettings(
            issuer_url=_UNPUBLISHED_ISSUER_URL,
            resource_server_url=None,
            required_scopes=["arcreel"],
        ),
        stateless_http=True,
        streamable_http_path="/",
        json_response=False,
        max_request_body_size=_MAX_REQUEST_BODY_BYTES,
        # 端点每请求强制 arc- API Key，且该 Key 从不以 cookie / session 形式存在于浏览器，
        # 重绑定到本端点的请求拿不到凭证、只能收 401——Host 白名单在此不构成安全边界，
        # 只会拦下合法部署；Host 归属由反向代理与部署形态承担。浏览器型客户端的跨源防护由
        # 应用级 CORSMiddleware（CORS_ORIGINS，见 server/cors_config.py）单点承担。
        # 关闭该开关不影响 SDK 对 POST 的 Content-Type 校验。
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


class RemoteMCPHost:
    """Stable ASGI mount whose one-shot SDK manager is rebuilt per host lifespan."""

    def __init__(self, server_factory: Callable[[], FastMCP] = build_remote_mcp_server) -> None:
        self._server_factory = server_factory
        self._app: Any | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._app is None:
            await PlainTextResponse("MCP server is not running", status_code=503)(scope, receive, send)
            return
        await self._app(scope, receive, send)

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[None]:
        server = self._server_factory()
        child_app = server.streamable_http_app()
        async with server.session_manager.run():
            self._app = child_app
            try:
                yield
            finally:
                self._app = None


remote_mcp_host = RemoteMCPHost()

REMOTE_MCP_PATH = "/mcp"


class _MountPrefixEndpoint:
    """把挂载前缀本身（无末尾斜杠）按 Mount 的子作用域交给同一子应用，代替 307 重定向。

    与 Starlette ``Mount`` 一致：``path`` 保留完整路径，只把前缀并入 ``root_path``，子应用经
    ``get_route_path`` 自行去掉前缀；``path`` 若改成 ``"/"``，会和 ``Mount`` 的子作用域不一致。
    """

    def __init__(self, prefix: str, app: ASGIApp) -> None:
        self._prefix = prefix
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        root_path = scope.get("root_path", "")
        child_scope = {
            **scope,
            "app_root_path": scope.get("app_root_path", root_path),
            "root_path": root_path + self._prefix,
            "path": scope["path"] + "/",
        }
        if "raw_path" in scope:
            child_scope["raw_path"] = scope["raw_path"] + b"/"
        await self._app(child_scope, receive, send)


def mount_remote_mcp(app: Starlette, mcp_app: ASGIApp) -> None:
    """把远程 MCP 端点挂到 ``/mcp``。

    MCP 规范以无末尾斜杠的 ``/mcp`` 为端点的规范形式，这里直接处理而不回 307：部分客户端不跟随
    POST 重定向，反向代理子路径部署下相对 ``Location`` 也会跳出前缀。``/mcp/`` 保留为兼容入口。
    """
    app.router.routes.extend(
        [
            Route(REMOTE_MCP_PATH, _MountPrefixEndpoint(REMOTE_MCP_PATH, mcp_app), include_in_schema=False),
            Mount(REMOTE_MCP_PATH, mcp_app),
        ]
    )


__all__ = [
    "REMOTE_MCP_PATH",
    "ArcApiKeyVerifier",
    "RemoteMCPHost",
    "build_remote_mcp_server",
    "mount_remote_mcp",
    "remote_mcp_host",
]
